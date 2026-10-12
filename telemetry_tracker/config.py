"""Settings, read from the environment and a local .env file (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def _flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _path(name: str, default: str) -> Path:
    path = Path(os.getenv(name) or default)
    return path if path.is_absolute() else ROOT / path


@dataclass(frozen=True)
class Settings:
    # LLM endpoint. ANTHROPIC_BASE_URL is optional: leave it blank for api.anthropic.com,
    # or point it at an internal gateway that serves the Anthropic Messages API.
    api_key: str | None
    base_url: str | None
    model: str
    effort: str
    refusal_fallback: bool

    # Where the telemetry comes from: "file" (OTLP JSON export) or "langfuse".
    source: str
    otlp_file: Path
    langfuse_host: str
    langfuse_public_key: str | None
    langfuse_secret_key: str | None
    langfuse_max_traces: int

    # Status is judged on the last `window_days`; patterns compare it with the last `baseline_days`.
    window_days: int
    baseline_days: int

    data_dir: Path
    out_dir: Path
    port: int

    @property
    def llm_configured(self) -> bool:
        return bool(self.api_key)


def load_settings(env_file: Path | None = None) -> Settings:
    load_dotenv(env_file or ROOT / ".env", override=False)
    return Settings(
        api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        base_url=os.getenv("ANTHROPIC_BASE_URL") or None,
        model=os.getenv("TT_MODEL") or "claude-opus-5-5",
        effort=os.getenv("TT_EFFORT") or "medium",
        refusal_fallback=_flag("TT_REFUSAL_FALLBACK", True),
        source=(os.getenv("TT_SOURCE") or "file").lower(),
        otlp_file=_path("TT_OTLP_FILE", "data/sample_telemetry.json"),
        langfuse_host=(os.getenv("LANGFUSE_HOST") or "https://cloud.langfuse.com").rstrip("/"),
        langfuse_public_key=os.getenv("LANGFUSE_PUBLIC_KEY") or None,
        langfuse_secret_key=os.getenv("LANGFUSE_SECRET_KEY") or None,
        langfuse_max_traces=int(os.getenv("TT_LANGFUSE_MAX_TRACES") or 500),
        window_days=int(os.getenv("TT_WINDOW_DAYS") or 7),
        baseline_days=int(os.getenv("TT_BASELINE_DAYS") or 28),
        data_dir=_path("TT_DATA_DIR", "data"),
        out_dir=_path("TT_OUT_DIR", "out"),
        port=int(os.getenv("TT_PORT") or 8000),
    )
