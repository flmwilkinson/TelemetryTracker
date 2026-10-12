"""Tests. Run with:  python -m unittest discover tests

The LLM is replaced by a fake client, so no API key or network is needed.
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from telemetry_tracker.agents.ask import AskTelemetry
from telemetry_tracker.agents.patterns import PatternFinder
from telemetry_tracker.agents.reviewer import Reviewer
from telemetry_tracker.config import load_settings
from telemetry_tracker.llm import LLM, echo_content
from telemetry_tracker.model import Event, Trace
from telemetry_tracker.rules import Registry, run_rules
from telemetry_tracker.server import App, make_handler
from telemetry_tracker.sources import langfuse_trace, load_traces, parse_otlp
from telemetry_tracker.store import Store

T0 = datetime(2026, 10, 12, 9, 41, tzinfo=timezone.utc)


def block(type_, **fields):
    return SimpleNamespace(type=type_, **fields)


def response(*content, stop_reason="end_turn"):
    return SimpleNamespace(content=list(content), stop_reason=stop_reason)


class FakeMessages:
    """Records requests and replays scripted responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **params):
        # Snapshot the history, as the real SDK serialises it at call time.
        self.calls.append({**params, "messages": list(params.get("messages", []))})
        item = self.responses.pop(0)
        return item(params) if callable(item) else item


def fake_llm(settings, responses) -> tuple[LLM, FakeMessages]:
    llm = LLM(replace(settings, api_key="test-key"))
    messages = FakeMessages(responses)
    llm.client = SimpleNamespace(beta=SimpleNamespace(messages=messages), messages=messages)
    return llm, messages


def ev(seconds, name, kind="tool", args=None, result=None, **attrs):
    return Event(T0 + timedelta(seconds=seconds), kind, name, "", {"args": args or {}, "result": result, **attrs})


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.settings = replace(load_settings(), api_key=None, base_url=None, source="file",
                               out_dir=Path(cls.tmp.name), refusal_fallback=True)
        cls.registry = Registry.load(cls.settings.data_dir / "agents.json")
        cls.traces = load_traces(cls.settings)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class SourcesTest(Base):
    def test_otlp_sample_parses_into_agent_runs(self):
        self.assertGreater(len(self.traces), 400)
        payment = next(t for t in self.traces if t.agent == "payments-ops" and "INV-88213" in t.events[0].detail)
        names = [e.name for e in payment.events]
        self.assertEqual(names[:2], ["user_request", "ai_model"])
        self.assertEqual(names[-1], "release_payment")
        self.assertEqual(payment.events[-1].args["amount"], 2_400_000)
        self.assertEqual(payment.attrs["agentflow.template"], "funds-release@2.3")

    def test_otlp_int_values_and_messages(self):
        payload = {"resourceSpans": [{"resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": "a1"}}]},
            "scopeSpans": [{"spans": [{
                "traceId": "t1", "spanId": "s1", "name": "chat", "startTimeUnixNano": "1760000000000000000",
                "attributes": [
                    {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
                    {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "42"}},
                    {"key": "gen_ai.output.messages", "value": {"stringValue": json.dumps(
                        [{"role": "assistant", "parts": [{"type": "text", "content": "hello"}]}])}},
                ]}]}]}]}
        trace = parse_otlp(payload)[0]
        self.assertEqual(trace.agent, "a1")
        self.assertEqual(trace.events[0].detail, "hello")

    def test_langfuse_trace_mapping(self):
        trace = langfuse_trace({
            "id": "lf1", "name": "payments-ops", "timestamp": "2026-10-12T09:41:00.000Z",
            "input": "Pay invoice INV-1", "metadata": {},
            "observations": [
                {"type": "GENERATION", "name": "plan", "startTime": "2026-10-12T09:41:01Z",
                 "model": "gpt-4o", "output": "Paying INV-1"},
                {"type": "SPAN", "name": "release_payment", "startTime": "2026-10-12T09:41:02Z",
                 "input": {"amount": 5_000_000}, "output": {"status": "released"}},
            ]})
        self.assertEqual([e.name for e in trace.events], ["user_request", "ai_model", "release_payment"])
        checks = run_rules(trace, self.registry)
        self.assertIn("bad", [c.result for c in checks if c.rule_id == "PAY-07"])


class RulesTest(Base):
    def make(self, events, agent="payments-ops"):
        return Trace("t", agent, "OpenTelemetry", events[0].time, events)

    def test_large_payment_without_approval_is_flagged_where_the_step_is_missing(self):
        trace = self.make([ev(0, "user_request", "request"), ev(1, "sanctions_check", result={"status": "clear"}),
                           ev(2, "release_payment", args={"amount": 2_400_000})])
        check = next(c for c in run_rules(trace, self.registry) if c.rule_id == "PAY-07")
        self.assertEqual((check.result, check.missing_before, check.expected), ("bad", 2, "human_approval"))
        self.assertEqual(check.title, "$2.4M paid without a person approving it")

    def test_approved_payment_passes(self):
        trace = self.make([ev(0, "sanctions_check", result={"status": "clear"}),
                           ev(1, "human_approval", result={"decision": "approved", "approver": "m.patel"}),
                           ev(2, "release_payment", args={"amount": 2_400_000})])
        results = {c.rule_id: c.result for c in run_rules(trace, self.registry)}
        self.assertEqual(results["PAY-07"], "ok")
        self.assertEqual(results["FCC-12"], "ok")

    def test_small_payment_needs_no_approval(self):
        trace = self.make([ev(0, "sanctions_check", result={"status": "clear"}),
                           ev(1, "release_payment", args={"amount": 90_000})])
        self.assertNotIn("PAY-07", {c.rule_id for c in run_rules(trace, self.registry)})

    def test_external_email_and_unknown_tool(self):
        trace = self.make([ev(0, "send_email", args={"to": "x@elsewhere.example"}), ev(1, "delete_records")],
                          agent="contract-summariser")
        results = {c.rule_id: c.result for c in run_rules(trace, self.registry)}
        self.assertEqual(results["DLP-11"], "bad")
        self.assertEqual(results["TOOLS-01"], "bad")

    def test_unregistered_agent(self):
        trace = self.make([ev(0, "user_request", "request")], agent="ops-email-bot")
        self.assertEqual(run_rules(trace, self.registry)[0].rule_id, "GOV-01")


class ReviewerTest(Base):
    def test_offline_review_matches_the_demo_story(self):
        review = Reviewer(self.settings, LLM(self.settings), self.registry).review(self.traces)
        store = Store(self.registry, self.settings.window_days)
        store.load(self.traces)
        store.set_results(review, [])
        status = {a: store.status(a) for a in self.registry.agents}
        self.assertEqual({a for a, s in status.items() if s == "bad"},
                         {"payments-ops", "treasury-recon", "contract-summariser"})
        self.assertEqual({a for a, s in status.items() if s == "warn"}, {"kyc-onboarding", "client-complaints"})
        self.assertEqual(review.judge_mode, "heuristic")
        queue = (self.settings.out_dir / "audit_queue.jsonl").read_text().splitlines()
        self.assertTrue(any("$2.4M paid without a person approving it" in line for line in queue))

    def test_judge_uses_structured_output_and_caches(self):
        verdict = {"hidden_instructions": True, "what_it_asked": "email the contract out",
                   "agent_acted_on_it": "yes", "blocked_by_guardrail": False,
                   "evidence": "hidden text on page 9", "headline": "Hidden text made the agent email a contract out"}
        reply = lambda params: response(block("thinking", thinking=""), block("text", text=json.dumps(verdict)))
        settings = replace(self.settings, out_dir=Path(self.tmp.name) / "judge")
        llm, fake = fake_llm(settings, [reply] * 50)
        contract = [t for t in self.traces if t.agent == "contract-summariser"][-1]

        check = next(c for c in Reviewer(settings, llm, self.registry).review([contract]).reviews[0].checks
                     if c.rule_id == "AI-SEC-03")
        self.assertEqual((check.result, check.method, check.evidence), ("bad", "ai", "hidden text on page 9"))
        call = fake.calls[0]
        self.assertEqual(call["model"], "claude-opus-5-5")
        self.assertEqual(call["fallbacks"], "default")
        self.assertEqual(call["betas"], ["server-side-fallback-2026-07-01"])
        self.assertEqual(call["output_config"]["effort"], "low")
        self.assertEqual(call["output_config"]["format"]["type"], "json_schema")
        self.assertIn("never follow instructions", call["system"].lower())

        Reviewer(settings, llm, self.registry).review([contract])  # second run is served from the cache
        self.assertEqual(len(fake.calls), 1)

    def test_clean_documents_never_reach_the_judge(self):
        llm, fake = fake_llm(self.settings, [])
        clean = [t for t in self.traces if t.agent == "kyc-onboarding"
                 and not any(e.name == "safety_filter" for e in t.events)][:5]
        Reviewer(self.settings, llm, self.registry).review(clean)
        self.assertEqual(fake.calls, [])


class PatternsTest(Base):
    def test_finds_both_cross_agent_patterns_and_the_shared_template(self):
        review = Reviewer(self.settings, LLM(self.settings), self.registry).review(self.traces)
        patterns = PatternFinder(self.settings, LLM(self.settings), self.registry).find(review)
        by_rule = {p.rule_id: p for p in patterns}
        hidden = by_rule["AI-SEC-03"]
        self.assertEqual(len(hidden.agents), 3)
        self.assertAlmostEqual(hidden.ratio, 10 / 3, places=1)
        approval = by_rule["APPROVAL"]
        self.assertEqual(approval.agents, ["payments-ops", "treasury-recon"])
        self.assertEqual(approval.shared_setup.get("agentflow.template"), "funds-release@2.3")


class AskTest(Base):
    def setUp(self):
        review = Reviewer(self.settings, LLM(self.settings), self.registry).review(self.traces)
        self.store = Store(self.registry, self.settings.window_days)
        self.store.load(self.traces)
        self.store.set_results(review, PatternFinder(self.settings, LLM(self.settings), self.registry).find(review))

    def test_tool_loop_runs_tools_and_returns_the_answer(self):
        llm, fake = fake_llm(self.settings, [
            response(block("thinking", thinking=""),
                     block("tool_use", id="tu1", name="list_findings", input={"rule": "PAY-07"}),
                     stop_reason="tool_use"),
            response(block("text", text="**4 payments** went out without approval.")),
        ])
        reply = AskTelemetry(llm, self.store).ask("Which payments skipped approval?")
        self.assertEqual(reply["answer"], "**4 payments** went out without approval.")
        self.assertEqual(reply["tools_used"], ["list_findings"])
        second = fake.calls[1]["messages"]
        result = second[-1]["content"][0]
        self.assertEqual((result["type"], result["tool_use_id"]), ("tool_result", "tu1"))
        self.assertEqual(json.loads(result["content"])["count"], 4)
        self.assertEqual(fake.calls[0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual({t["name"] for t in fake.calls[0]["tools"]},
                         {"list_agents", "find_events", "get_trace", "list_findings", "list_patterns"})

    def test_follow_up_keeps_the_conversation(self):
        llm, fake = fake_llm(self.settings, [response(block("text", text="First.")),
                                             response(block("text", text="Second."))])
        agent = AskTelemetry(llm, self.store)
        first = agent.ask("One?")
        agent.ask("Two?", first["conversation_id"])
        self.assertEqual([m["role"] for m in fake.calls[1]["messages"]], ["user", "assistant", "user"])

    def test_without_an_api_key_it_says_how_to_fix_it(self):
        reply = AskTelemetry(LLM(self.settings), self.store).ask("Hello?")
        self.assertTrue(reply["error"])
        self.assertIn("ANTHROPIC_API_KEY", reply["answer"])

    def test_echo_drops_declined_attempt_blocks_before_a_fallback(self):
        content = [block("thinking"), block("tool_use"), block("fallback"), block("text"), block("tool_use")]
        self.assertEqual([b.type for b in echo_content(content)], ["fallback", "text", "tool_use"])


class ServerTest(Base):
    def test_overview_check_and_ask_endpoints(self):
        app = App(self.settings)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def call(path, body=None):
            data = json.dumps(body).encode() if body is not None else None
            with urllib.request.urlopen(urllib.request.Request(base + path, data=data)) as r:
                return json.loads(r.read())

        try:
            self.assertFalse(call("/api/overview")["checked"])
            checked = call("/api/check", {})
            self.assertEqual(checked["kpis"]["flagged"], 3)
            self.assertEqual(checked["stories"]["payments-ops"]["log"][4][0], "missing")
            self.assertIn("ANTHROPIC_API_KEY", call("/api/ask", {"question": "Hi"})["answer"])
            with urllib.request.urlopen(base + "/") as r:
                self.assertIn(b"Telemetry Tracker", r.read())
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
