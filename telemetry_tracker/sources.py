"""Telemetry readers. Each one returns a list of `Trace`.

- OpenTelemetry: OTLP/JSON as written by the OpenTelemetry Collector's file exporter, with the
  GenAI semantic conventions (gen_ai.operation.name, gen_ai.tool.name, gen_ai.tool.call.arguments ...).
- Langfuse: the public REST API (GET /api/public/traces and /api/public/traces/{id}).
"""
from __future__ import annotations

import base64
import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .model import Event, Trace

MONEY_KEYS = {"amount", "value", "total", "difference"}
QUIET_KEYS = {"text", "content", "body", "currency", "subject", "date"}


def load_traces(settings: Settings) -> list[Trace]:
    if settings.source == "langfuse":
        since = datetime.now(timezone.utc) - timedelta(days=settings.baseline_days)
        return load_langfuse(settings, since)
    return load_otlp_file(settings.otlp_file)


# ---------------------------------------------------------------- shared helpers

def money(value: Any) -> str:
    try:
        return f"${float(value):,.0f}"
    except (TypeError, ValueError):
        return str(value)


def describe(args: Any, result: Any) -> str:
    """A one-line summary of a tool call, e.g. "$2,400,000, Northwind Ltd: released"."""
    parts: list[str] = []
    if isinstance(args, dict):
        for key, value in args.items():
            if key in QUIET_KEYS or value in (None, ""):
                continue
            parts.append(money(value) if key in MONEY_KEYS else str(value))
    elif args:
        parts.append(str(args))
    summary = ", ".join(parts[:2])
    outcome = None
    if isinstance(result, dict):
        outcome = next((result[k] for k in ("status", "decision", "action", "summary") if result.get(k)), None)
    elif isinstance(result, str):
        outcome = result
    if outcome:
        summary = f"{summary}: {outcome}" if summary else f"Result: {outcome}"
    return summary


def _first_text(messages: Any) -> str:
    """Text from GenAI-convention messages ([{role, parts:[{type, content}]}]) or a plain string."""
    if isinstance(messages, str):
        try:
            messages = json.loads(messages)
        except json.JSONDecodeError:
            return messages
    if isinstance(messages, dict):
        messages = [messages]
    for message in messages or []:
        if isinstance(message, str):
            return message
        for part in message.get("parts") or []:
            if part.get("type") == "text" and part.get("content"):
                return str(part["content"])
        if isinstance(message.get("content"), str):
            return message["content"]
    return ""


def _maybe_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


# ---------------------------------------------------------------- OpenTelemetry (OTLP/JSON)

def _otlp_value(value: dict) -> Any:
    if "stringValue" in value:
        return value["stringValue"]
    if "intValue" in value:
        return int(value["intValue"])  # int64 is a string in OTLP/JSON
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "boolValue" in value:
        return bool(value["boolValue"])
    if "arrayValue" in value:
        return [_otlp_value(v) for v in value["arrayValue"].get("values", [])]
    if "kvlistValue" in value:
        return _otlp_attrs(value["kvlistValue"].get("values", []))
    return None


def _otlp_attrs(attributes: list[dict]) -> dict[str, Any]:
    return {a["key"]: _otlp_value(a.get("value", {})) for a in attributes or []}


def _nanos(value: Any) -> datetime:
    return datetime.fromtimestamp(int(value) / 1e9, tz=timezone.utc)


def _otlp_event(span: dict, attrs: dict) -> Event:
    when = _nanos(span["startTimeUnixNano"])
    op = attrs.get("gen_ai.operation.name")
    if op == "invoke_agent":
        text = _first_text(attrs.get("gen_ai.input.messages"))
        return Event(when, "request", "user_request", f'"{text}"' if text else "", {"text": text})
    if op in {"chat", "text_completion", "generate_content"}:
        text = _first_text(attrs.get("gen_ai.output.messages"))
        model = attrs.get("gen_ai.response.model") or attrs.get("gen_ai.request.model")
        return Event(when, "model", "ai_model", text, {"text": text, "model": model})
    if op == "execute_tool":
        name = attrs.get("gen_ai.tool.name") or span.get("name", "tool")
        args = _maybe_json(attrs.get("gen_ai.tool.call.arguments")) or {}
        result = _maybe_json(attrs.get("gen_ai.tool.call.result"))
        return Event(when, "tool", name, describe(args, result), {"args": args, "result": result})
    return Event(when, "span", span.get("name", "span"), "", {"raw": attrs})


def parse_otlp(payload: dict) -> list[Trace]:
    spans: dict[str, list[tuple[dict, dict, dict]]] = {}
    for resource_spans in payload.get("resourceSpans", []):
        resource = _otlp_attrs(resource_spans.get("resource", {}).get("attributes", []))
        for scope in resource_spans.get("scopeSpans", []):
            for span in scope.get("spans", []):
                spans.setdefault(span["traceId"], []).append((span, _otlp_attrs(span.get("attributes", [])), resource))

    traces = []
    for trace_id, items in spans.items():
        items.sort(key=lambda item: int(item[0]["startTimeUnixNano"]))
        resource = items[0][2]
        agent = resource.get("service.name") or next(
            (a.get("gen_ai.agent.name") for _, a, _ in items if a.get("gen_ai.agent.name")), "unknown"
        )
        events = [_otlp_event(span, attrs) for span, attrs, _ in items]
        traces.append(Trace(trace_id, agent, "OpenTelemetry", events[0].time, events, resource))
    traces.sort(key=lambda t: t.start)
    return traces


def load_otlp_file(path: Path) -> list[Trace]:
    """Read an OTLP/JSON export: one JSON object, or JSON lines (one object per line)."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        payloads = [json.loads(text)]
    except json.JSONDecodeError:
        payloads = [json.loads(line) for line in text.splitlines() if line.strip()]
    merged: dict = {"resourceSpans": []}
    for payload in payloads:
        merged["resourceSpans"].extend(payload.get("resourceSpans", []))
    return parse_otlp(merged)


# ---------------------------------------------------------------- Langfuse

def _iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _langfuse_get(settings: Settings, path: str, query: dict | None = None) -> dict:
    url = f"{settings.langfuse_host}{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    token = base64.b64encode(f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}".encode()).decode()
    request = urllib.request.Request(url, headers={"Authorization": f"Basic {token}", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def langfuse_trace(detail: dict) -> Trace:
    """Convert one Langfuse trace (with its observations) into a Trace."""
    metadata = detail.get("metadata") or {}
    agent = metadata.get("agent") or detail.get("name") or "unknown"
    start = _iso(detail["timestamp"])
    events = []
    request = _first_text(detail.get("input")) if detail.get("input") is not None else ""
    if request:
        events.append(Event(start, "request", "user_request", f'"{request}"', {"text": request}))
    for obs in sorted(detail.get("observations") or [], key=lambda o: o.get("startTime") or ""):
        when = _iso(obs.get("startTime") or detail["timestamp"])
        if obs.get("type") == "GENERATION":
            text = _first_text(obs.get("output")) if obs.get("output") is not None else ""
            events.append(Event(when, "model", "ai_model", text, {"text": text, "model": obs.get("model")}))
        else:
            args, result = obs.get("input") or {}, obs.get("output")
            events.append(Event(when, "tool", obs.get("name") or "step", describe(args, result), {"args": args, "result": result}))
    attrs = {"langfuse.release": detail.get("release"), "langfuse.version": detail.get("version"), **metadata}
    return Trace(detail["id"], agent, "Langfuse", start, events, attrs)


def load_langfuse(settings: Settings, since: datetime) -> list[Trace]:
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        raise RuntimeError("Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY in .env to read from Langfuse.")
    summaries, page = [], 1
    while len(summaries) < settings.langfuse_max_traces:
        body = _langfuse_get(settings, "/api/public/traces", {
            "page": page, "limit": 100, "fromTimestamp": since.isoformat().replace("+00:00", "Z"),
        })
        summaries.extend(body.get("data", []))
        if page >= (body.get("meta") or {}).get("totalPages", 1):
            break
        page += 1
    traces = [langfuse_trace(_langfuse_get(settings, f"/api/public/traces/{s['id']}"))
              for s in summaries[: settings.langfuse_max_traces]]
    traces.sort(key=lambda t: t.start)
    return traces
