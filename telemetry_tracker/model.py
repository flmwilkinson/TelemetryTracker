"""The normalised shape every telemetry source is converted into."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

SEVERITY = {"ok": 0, "warn": 1, "bad": 2}


@dataclass
class Event:
    """One step an agent took, as its log recorded it."""

    time: datetime
    kind: str  # request | model | tool | span
    name: str  # user_request, ai_model, or the tool name (release_payment, sanctions_check, ...)
    detail: str  # short human-readable summary
    attrs: dict[str, Any] = field(default_factory=dict)  # args, result, model, text, ...

    @property
    def args(self) -> dict:
        return self.attrs.get("args") or {}

    @property
    def result(self) -> Any:
        return self.attrs.get("result")


@dataclass
class Trace:
    """One run of one agent: the user's request and everything the agent did for it."""

    id: str
    agent: str  # agent id, e.g. payments-ops (OTel service.name / Langfuse trace name)
    source: str  # OpenTelemetry | Langfuse
    start: datetime
    events: list[Event]
    attrs: dict[str, Any] = field(default_factory=dict)  # resource attributes (deployment, template, ...)


@dataclass
class Check:
    """The result of testing one rule against one trace."""

    rule_id: str
    label: str  # plain-English rule, e.g. "Over $1M approved by a person"
    result: str  # ok | warn | bad
    evidence: str  # short pointer into the log, e.g. "sanctions_check at 09:41:03"
    title: str | None = None  # plain-English headline when the rule is not met
    at: int | None = None  # index of the event in the trace this check is about
    missing_before: int | None = None  # index of the event an expected step should have preceded
    expected: str | None = None  # name of the missing step, e.g. human_approval
    method: str = "rule"  # rule | ai | heuristic


@dataclass
class Finding:
    id: str
    agent: str
    trace_id: str
    rule_id: str
    result: str
    title: str
    evidence: str
    time: datetime
