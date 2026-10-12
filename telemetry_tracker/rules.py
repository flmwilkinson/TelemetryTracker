"""Fixed rules, tested against every trace. Cheap enough to run on every single action.

Each rule returns `Check`s. A log only records what happened, so a skipped step (such as a
missing approval) is found by its absence: the rule expects an event that is not there, and
`missing_before` marks where it should have been.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .model import Check, Event, Trace
from .sources import money


@dataclass(frozen=True)
class Registry:
    """The AI inventory: which agents exist, who owns them, what they may use."""

    agents: dict[str, dict]
    approved_models: set[str]
    approved_email_domains: set[str]

    @classmethod
    def load(cls, path: Path) -> "Registry":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            agents={a["id"]: a for a in data["agents"]},
            approved_models=set(data["approved_models"]),
            approved_email_domains=set(data["approved_email_domains"]),
        )

    def name(self, agent_id: str) -> str:
        return self.agents.get(agent_id, {}).get("name", agent_id)


@dataclass(frozen=True)
class ApprovalRule:
    rule_id: str
    tool: str
    min_amount: float
    label: str
    title: str  # formatted with {amount}


APPROVAL_RULES = [
    ApprovalRule("PAY-07", "release_payment", 1_000_000, "Over $1M approved by a person",
                 "{amount} paid without a person approving it"),
    ApprovalRule("TRY-03", "release_funds", 100_000, "Release approved by a person",
                 "{amount} released without a person approving it"),
]
APPROVAL_TOOL = "human_approval"


def hhmmss(event: Event) -> str:
    return event.time.strftime("%H:%M:%S")


def _amount(event: Event) -> float:
    for key in ("amount", "value", "total"):
        try:
            return float(event.args[key])
        except (KeyError, TypeError, ValueError):
            continue
    return 0.0


def _result_field(event: Event, key: str) -> str:
    return str(event.result.get(key, "")).lower() if isinstance(event.result, dict) else ""


def check_inventory(trace: Trace, reg: Registry) -> list[Check]:
    if trace.agent in reg.agents:
        return []
    return [Check("GOV-01", "Agent is in the AI inventory", "bad", "no inventory record",
                  title="AI agent running without an inventory record")]


def check_approvals(trace: Trace, reg: Registry) -> list[Check]:
    checks = []
    for rule in APPROVAL_RULES:
        for i, event in enumerate(trace.events):
            if event.name != rule.tool or _amount(event) < rule.min_amount:
                continue
            approval = next((e for e in trace.events[:i]
                             if e.name == APPROVAL_TOOL and _result_field(e, "decision") == "approved"), None)
            if approval:
                who = approval.result.get("approver", "a person")
                checks.append(Check(rule.rule_id, rule.label, "ok", f"{APPROVAL_TOOL} at {hhmmss(approval)} by {who}"))
            else:
                checks.append(Check(rule.rule_id, rule.label, "bad", "no approval in the log",
                                    title=rule.title.format(amount=short_money(_amount(event))),
                                    at=i, missing_before=i, expected=APPROVAL_TOOL))
    return checks


def short_money(amount: float) -> str:
    """$2.4M, $450k, $900"""
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M".replace(".0M", "M")
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}k"
    return money(amount)


def check_sanctions(trace: Trace, reg: Registry) -> list[Check]:
    checks = []
    for i, event in enumerate(trace.events):
        if event.name != "release_payment":
            continue
        screen = next((e for e in trace.events[:i] if e.name == "sanctions_check"), None)
        if screen is None:
            checks.append(Check("FCC-12", "Supplier screened before paying", "bad", "no screening in the log",
                                title="Payment sent without a sanctions check", at=i, missing_before=i,
                                expected="sanctions_check"))
        elif _result_field(screen, "status") != "clear":
            checks.append(Check("FCC-12", "Supplier screened before paying", "bad",
                                f"sanctions_check at {hhmmss(screen)} was not clear",
                                title="Payment sent to a possible sanctions match", at=i))
        else:
            checks.append(Check("FCC-12", "Supplier screened before paying", "ok", f"sanctions_check at {hhmmss(screen)}"))
    return checks


def check_tools(trace: Trace, reg: Registry) -> list[Check]:
    allowed = set(reg.agents.get(trace.agent, {}).get("allowed_tools", []))
    used = sorted({e.name for e in trace.events if e.kind == "tool"})
    if not used or trace.agent not in reg.agents:
        return []
    off_list = [name for name in used if name not in allowed]
    if off_list:
        at = next(i for i, e in enumerate(trace.events) if e.name == off_list[0])
        return [Check("TOOLS-01", "Only approved tools used", "bad", f"{off_list[0]} is not on the list",
                      title=f"Agent used a tool it is not allowed to use ({off_list[0]})", at=at)]
    return [Check("TOOLS-01", "Only approved tools used", "ok", f"{len(used)} of {len(used)} on the list")]


def check_models(trace: Trace, reg: Registry) -> list[Check]:
    models = sorted({e.attrs.get("model") for e in trace.events if e.kind == "model" and e.attrs.get("model")})
    if not models:
        return []
    unapproved = [m for m in models if m not in reg.approved_models]
    if unapproved:
        at = next(i for i, e in enumerate(trace.events) if e.attrs.get("model") == unapproved[0])
        return [Check("MRM-01", "Only approved AI model used", "bad", f"{unapproved[0]} is not approved",
                      title=f"Agent used an unapproved AI model ({unapproved[0]})", at=at)]
    return [Check("MRM-01", "Only approved AI model used", "ok", f"{models[0]}, approved")]


def check_email(trace: Trace, reg: Registry) -> list[Check]:
    sends = [e for e in trace.events if e.name == "send_email"]
    if not sends:
        return []
    for i, event in enumerate(trace.events):
        if event.name != "send_email":
            continue
        domain = str(event.args.get("to", "")).rsplit("@", 1)[-1].lower()
        if domain not in reg.approved_email_domains:
            return [Check("DLP-11", "Emails only to bank addresses", "bad", f"sent outside at {hhmmss(event)}",
                          title="Data emailed to an address outside the bank", at=i)]
    return [Check("DLP-11", "Emails only to bank addresses", "ok", "all to bank addresses")]


RULES = [check_inventory, check_approvals, check_sanctions, check_tools, check_models, check_email]


def run_rules(trace: Trace, reg: Registry) -> list[Check]:
    return [check for rule in RULES for check in rule(trace, reg)]
