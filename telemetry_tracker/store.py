"""Holds the latest telemetry and check results.

Builds what the dashboard shows, and answers the questions the Ask Telemetry agent's tools ask.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .agents.patterns import Pattern
from .agents.reviewer import Review, TraceReview
from .model import SEVERITY, Trace
from .rules import Registry

MAX_ROWS = 9  # agents shown on the dashboard
MAX_LOG_ROWS = 7
MAX_CHECKS = 3


def _clip(value, limit: int = 300):
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + "..."


class Store:
    def __init__(self, registry: Registry, window_days: int):
        self.registry = registry
        self.window_days = window_days
        self.traces: list[Trace] = []
        self.review: Review | None = None
        self.patterns: list[Pattern] = []
        self.checked_at: datetime | None = None

    # ------------------------------------------------------------------ updates

    def load(self, traces: list[Trace]) -> None:
        self.traces = traces

    def set_results(self, review: Review, patterns: list[Pattern]) -> None:
        self.review, self.patterns = review, patterns
        self.checked_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------------ helpers

    @property
    def now(self) -> datetime:
        if self.review:
            return self.review.now
        return max((t.start for t in self.traces), default=datetime.now(timezone.utc))

    def _recent(self, days: int | None = None) -> list[Trace]:
        since = self.now - timedelta(days=days or self.window_days)
        return [t for t in self.traces if t.start > since]

    def _agent_ids(self) -> list[str]:
        seen = {t.agent for t in self.traces}
        ordered = [a for a in self.registry.agents if a in seen]
        return ordered + sorted(seen - set(ordered))

    def _match_agent(self, query: str | None) -> set[str] | None:
        if not query:
            return None
        q = query.lower()
        return {a for a in self._agent_ids() if q in a.lower() or q in self.registry.name(a).lower()}

    def _reviews(self, agent: str, days: int | None = None) -> list[TraceReview]:
        if not self.review:
            return []
        return [r for r in self.review.reviews
                if r.trace.agent == agent and self.review.in_window(r.trace.start, days)]

    def status(self, agent: str) -> str:
        return max((r.result for r in self._reviews(agent)), key=SEVERITY.get, default="ok")

    def _resolve_trace(self, trace_id: str) -> TraceReview | None:
        if not self.review:
            return None
        return next((r for r in self.review.reviews if r.trace.id.startswith(trace_id)), None)

    # ------------------------------------------------------------------ dashboard

    def overview(self, llm_description: str) -> dict:
        recent = self._recent()
        agents = self._agent_ids()
        depts = {self.registry.agents.get(a, {}).get("department", "Unknown") for a in agents}
        actions = sum(len(t.events) for t in recent)
        data = {
            "mode": "live",
            "llm": llm_description,
            "sources": sorted({t.source for t in self.traces}),
            "checked": self.review is not None,
            "kpis": {
                "agents": len(agents), "agents_sub": f"across {len(depts)} business areas",
                "actions": actions, "actions_label": f"Actions checked, last {self.window_days} days",
                "actions_sub": "every one, against the rules",
                "flagged": None, "flagged_sub": "waiting for check",
            },
            "agents": [],
            "stories": {},
            "pattern": None,
        }
        if not self.review:
            data["agents"] = [self._row(a, "pending") for a in agents[:MAX_ROWS]]
            data["more"] = f"Showing {min(len(agents), MAX_ROWS)} of {len(agents)} agents"
            return data

        rows = [self._row(a, self.status(a)) for a in agents]
        if len(rows) > MAX_ROWS:
            rows = sorted(rows, key=lambda r: SEVERITY[r["st"]], reverse=True)[:MAX_ROWS]
        problem_agents = [a for a in agents if self.status(a) == "bad"]
        sent = len({f.id for f in self.review.findings() if f.result == "bad"})
        data["kpis"]["flagged"] = len(problem_agents)
        data["kpis"]["flagged_sub"] = f"{sent} findings sent to the audit team"
        data["agents"] = rows
        data["more"] = f"Showing {len(rows)} of {len(agents)} agents"
        data["stories"] = {r["id"]: self.story(r["id"]) for r in rows}
        data["judge"] = {"mode": self.review.judge_mode, "note": self.review.judge_note}
        if self.patterns:
            top = self.patterns[0]
            pattern_agents = set(top.agents)
            for row in data["agents"]:
                row["pat"] = row["id"] in pattern_agents
            data["pattern"] = {
                "tag": f"Same problem in {len(top.agents)} agents",
                "title": top.title, "desc": top.description,
                "agents": [self.registry.name(a) for a in top.agents],
                "cause": top.likely_cause,
                "incidents": [i["name"] for i in top.incidents],
            }
        return data

    def _row(self, agent: str, status: str) -> dict:
        info = self.registry.agents.get(agent, {})
        return {"id": agent, "name": info.get("name", agent), "dept": info.get("department", "Not in inventory"),
                "st": status, "pat": False}

    def story(self, agent: str) -> dict:
        """The run to show for one agent: its worst recent run, or its latest one."""
        reviews = self._reviews(agent) or self._reviews(agent, days=10_000)
        if not reviews:
            return {}
        chosen = max(reviews, key=lambda r: (SEVERITY[r.result], r.trace.start))
        trace = chosen.trace
        info = self.registry.agents.get(agent, {})
        actions = sum(len(r.trace.events) for r in self._reviews(agent))

        marks: dict[int, str] = {}
        missing: dict[int, str] = {}
        for check in chosen.checks:
            if check.result != "ok" and check.at is not None:
                if SEVERITY[check.result] > SEVERITY.get(marks.get(check.at, "ok"), 0):
                    marks[check.at] = check.result
            if check.missing_before is not None:
                missing[check.missing_before] = check.expected or "expected_step"
        rows = []
        for i, event in enumerate(trace.events):
            if i in missing:
                rows.append(["missing", "", missing[i], "Expected here, not in the log"])
            rows.append([marks.get(i, "ok"), event.time.strftime("%H:%M:%S"), event.name, event.detail])
        if len(rows) > MAX_LOG_ROWS:
            rows = rows[:1] + rows[-(MAX_LOG_ROWS - 1):]

        order = {id(c): n for n, c in enumerate(chosen.checks)}
        shown = sorted(chosen.checks, key=lambda c: SEVERITY[c.result], reverse=True)[:MAX_CHECKS]
        shown.sort(key=lambda c: order[id(c)])
        status = chosen.result
        return {
            "name": info.get("name", agent),
            "meta": f"{info.get('department', 'Not in inventory')} · {actions:,} actions checked, last {self.window_days} days",
            "source": trace.source, "trace_id": trace.id,
            "when": "Worst recent run" if status != "ok" else "Latest run",
            "status": status,
            "log": rows,
            "checks": [[c.result, c.label, c.evidence] for c in shown],
            "more": len(chosen.checks) - len(shown),
            "title": chosen.title,
            "rule": "Every rule passed. Checked again every 15 minutes." if status == "ok" else None,
            "escalated": trace.id in self.review.escalated,
        }

    # ------------------------------------------------------------------ Ask Telemetry tools

    def agents_summary(self) -> dict:
        out = []
        for agent in self._agent_ids():
            info = self.registry.agents.get(agent, {})
            reviews = self._reviews(agent)
            out.append({
                "id": agent, "name": info.get("name", agent), "department": info.get("department"),
                "owner": info.get("owner"), "in_ai_inventory": agent in self.registry.agents,
                "status": self.status(agent) if self.review else "not checked yet",
                "runs": len(reviews), "actions": sum(len(r.trace.events) for r in reviews),
                "findings": sum(1 for r in reviews for c in r.checks if c.result != "ok"),
            })
        return {"period_days": self.window_days, "agents": out}

    def find_events(self, agent: str | None = None, step: str | None = None, text: str | None = None,
                    days: int | None = None, limit: int = 30) -> dict:
        agents = self._match_agent(agent)
        traces = self._recent(days)
        searched, matches = 0, []
        for trace in traces:
            if agents is not None and trace.agent not in agents:
                continue
            for event in trace.events:
                searched += 1
                if step and step.lower() not in event.name.lower():
                    continue
                blob = f"{event.detail} {json.dumps(event.attrs, default=str)}".lower()
                if text and text.lower() not in blob:
                    continue
                matches.append({"trace_id": trace.id, "agent": self.registry.name(trace.agent),
                                "time": event.time.isoformat(timespec="seconds"), "step": event.name,
                                "detail": _clip(event.detail, 160)})
        return {"period_days": days or self.window_days, "actions_searched": searched,
                "matches": len(matches), "results": matches[-limit:]}

    def get_trace(self, trace_id: str) -> dict:
        review = self._resolve_trace(trace_id)
        if review is None:
            return {"error": f"No trace starting with '{trace_id}'."}
        t = review.trace
        return {
            "trace_id": t.id, "agent": self.registry.name(t.agent), "source": t.source,
            "start": t.start.isoformat(timespec="seconds"), "setup": t.attrs,
            "steps": [{"time": e.time.strftime("%H:%M:%S"), "step": e.name, "detail": _clip(e.detail, 200),
                       "args": _clip(e.args, 300), "result": _clip(e.result, 300)} for e in t.events],
            "checks": [{"rule": c.rule_id, "label": c.label, "result": c.result, "evidence": c.evidence,
                        "title": c.title, "method": c.method} for c in review.checks],
            "sent_to_audit_team": t.id in self.review.escalated,
        }

    def list_findings(self, agent: str | None = None, rule: str | None = None, result: str | None = None,
                      days: int | None = None) -> dict:
        if not self.review:
            return {"error": "No check has run yet."}
        agents = self._match_agent(agent)
        found = [f for f in self.review.findings(days)
                 if (agents is None or f.agent in agents)
                 and (not rule or rule.lower() in f.rule_id.lower())
                 and (not result or f.result == result)]
        found.sort(key=lambda f: f.time, reverse=True)
        return {"period_days": days or self.window_days, "count": len(found), "findings": [
            {"time": f.time.isoformat(timespec="seconds"), "agent": self.registry.name(f.agent), "rule": f.rule_id,
             "result": f.result, "title": f.title, "evidence": f.evidence, "trace_id": f.trace_id}
            for f in found[:40]]}

    def list_patterns(self) -> dict:
        return {"patterns": [{
            "problem": p.label, "title": p.title, "description": p.description,
            "agents": [self.registry.name(a) for a in p.agents], "recent_cases": p.recent,
            "usual_per_week": round(p.usual_per_week, 1), "shared_setup": p.shared_setup,
            "likely_cause": p.likely_cause, "known_incident_types": [i["name"] for i in p.incidents],
            "example_trace_ids": [f.trace_id for f in p.examples[:5]],
        } for p in self.patterns]}
