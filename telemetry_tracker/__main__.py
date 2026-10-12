"""Command line.

    python -m telemetry_tracker serve           dashboard and API at http://127.0.0.1:8000
    python -m telemetry_tracker check           run the reviewer and pattern agents, print the results
    python -m telemetry_tracker ask "question"  ask the Ask Telemetry agent one question
    python -m telemetry_tracker sample          regenerate data/sample_telemetry.json
"""
from __future__ import annotations

import argparse

from .config import load_settings

ICON = {"ok": "OK     ", "warn": "WATCH  ", "bad": "PROBLEM"}


def main() -> None:
    parser = argparse.ArgumentParser(prog="telemetry_tracker", description="Continuous assurance over AI agents.")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the dashboard and API")
    serve.add_argument("--host", default="127.0.0.1")
    sub.add_parser("check", help="run the reviewer and pattern agents")
    ask = sub.add_parser("ask", help="ask a question about the agents")
    ask.add_argument("question")
    sub.add_parser("sample", help="regenerate the sample telemetry file")
    args = parser.parse_args()

    settings = load_settings()

    if args.command == "sample":
        from .sample import write
        print(f"Wrote {write(settings.data_dir / 'sample_telemetry.json')}")
        return

    from .server import App, serve as run_server

    if args.command == "serve":
        run_server(settings, host=args.host)
        return

    app = App(settings)
    overview = app.check()

    if args.command == "check":
        k = overview["kpis"]
        print(f"LLM: {overview['llm']}   AI checks: {overview['judge']['mode']}")
        if overview["judge"].get("note"):
            print(f"  note: {overview['judge']['note']}")
        print(f"{k['agents']} agents, {k['actions']:,} actions checked in the last {settings.window_days} days. "
              f"{k['flagged']} agents need an auditor ({k['flagged_sub']}).\n")
        for row in overview["agents"]:
            story = overview["stories"][row["id"]]
            print(f"  {ICON[row['st']]}  {row['name']:<22} {story['title']}")
        print()
        for p in app.store.patterns:
            print(f"Pattern across {len(p.agents)} agents: {p.title}")
            print(f"  {p.description}")
            if p.likely_cause:
                print(f"  Likely cause: {p.likely_cause}")
        print(f"\nRule breaks were sent to the audit team queue: {settings.out_dir / 'audit_queue.jsonl'}")
        return

    if args.command == "ask":
        reply = app.ask(args.question, None)
        print(reply["answer"])


if __name__ == "__main__":
    main()
