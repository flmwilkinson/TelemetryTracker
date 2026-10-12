"""A small web server: serves the dashboard (index.html) and the agents' JSON API.

GET  /api/overview          agents, statuses, the run to show for each agent, top pattern
POST /api/check             run the reviewer and pattern agents now
POST /api/ask               {"question": "...", "conversation_id": optional} -> Ask Telemetry
GET  /api/traces/<id>       one trace with its checks
"""
from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .agents.ask import AskTelemetry
from .agents.patterns import PatternFinder
from .agents.reviewer import Reviewer
from .config import ROOT, Settings
from .llm import LLM
from .rules import Registry
from .sources import load_traces
from .store import Store


class App:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.llm = LLM(settings)
        self.registry = Registry.load(settings.data_dir / "agents.json")
        self.store = Store(self.registry, settings.window_days)
        self.reviewer = Reviewer(settings, self.llm, self.registry)
        self.pattern_finder = PatternFinder(settings, self.llm, self.registry)
        self.ask_agent = AskTelemetry(self.llm, self.store)
        self._lock = threading.Lock()
        self.store.load(load_traces(settings))

    def overview(self) -> dict:
        data = self.store.overview(self.llm.describe())
        sample = self.settings.source == "file" and self.settings.otlp_file.name.startswith("sample")
        data["data_label"] = "Sample telemetry" if sample else "Live telemetry"
        return data

    def check(self) -> dict:
        with self._lock:
            traces = load_traces(self.settings)
            self.store.load(traces)
            review = self.reviewer.review(traces)
            self.store.set_results(review, self.pattern_finder.find(review))
        return self.overview()

    def ask(self, question: str, conversation_id: str | None) -> dict:
        if self.store.review is None:
            self.check()
        return self.ask_agent.ask(question, conversation_id)


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                return self._file(ROOT / "index.html")
            if path.startswith("/assets/"):
                return self._file(ROOT / path.lstrip("/"), within=ROOT / "assets")
            if path == "/api/overview":
                return self._run(app.overview)
            if path.startswith("/api/traces/"):
                return self._run(lambda: app.store.get_trace(path.rsplit("/", 1)[-1]))
            self._json({"error": "Not found"}, 404)

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/check":
                return self._run(app.check)
            if path == "/api/ask":
                body = self._body()
                question = str(body.get("question") or "").strip()
                if not question:
                    return self._json({"error": "Ask a question."}, 400)
                return self._run(lambda: app.ask(question[:2000], body.get("conversation_id")))
            self._json({"error": "Not found"}, 404)

        # -------------------------------------------------------------- helpers

        def _run(self, fn):
            try:
                self._json(fn())
            except Exception as e:  # report config problems (bad .env, unreachable Langfuse) to the page
                self._json({"error": str(e)}, 500)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return {}

        def _json(self, data, status: int = 200):
            payload = json.dumps(data, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _file(self, path, within=None):
            path = path.resolve()
            if within is not None and within.resolve() not in path.parents:
                return self._json({"error": "Not found"}, 404)
            if not path.is_file():
                return self._json({"error": "Not found"}, 404)
            payload = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt, *args):
            if "/api/" in (args[0] if args else ""):
                super().log_message(fmt, *args)

    return Handler


def serve(settings: Settings, host: str = "127.0.0.1") -> None:
    app = App(settings)
    server = ThreadingHTTPServer((host, settings.port), make_handler(app))
    print(f"Telemetry Tracker running at http://{host}:{settings.port}")
    print(f"  telemetry: {settings.source} ({len(app.store.traces)} runs loaded)")
    print(f"  LLM:       {app.llm.describe()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
