"""The one place that talks to the LLM.

Every agent goes through `LLM.create()`, so the endpoint, model, effort and refusal fallback
are configured once, from .env.
"""
from __future__ import annotations

import json
from typing import Any

import anthropic

from .config import Settings

# Server-side refusal fallback: if the model declines a request, the API re-runs it on a
# recommended fallback model inside the same call. Turn off with TT_REFUSAL_FALLBACK=false
# if your gateway rejects beta parameters.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    """The LLM could not be used (not configured, unreachable, rejected the request, refused)."""


class LLM:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client: anthropic.Anthropic | None = None
        if settings.llm_configured:
            self.client = anthropic.Anthropic(
                api_key=settings.api_key,
                # Explicit default: a blank ANTHROPIC_BASE_URL= in .env must not reach the SDK as "".
                base_url=settings.base_url or "https://api.anthropic.com",
                max_retries=3,
            )

    @property
    def available(self) -> bool:
        return self.client is not None

    def describe(self) -> str:
        if not self.available:
            return "offline (no API key in .env)"
        where = self.settings.base_url or "api.anthropic.com"
        return f"{self.settings.model} via {where}"

    def create(self, *, effort: str | None = None, **params: Any):
        """Send one Messages API request and return the response."""
        if self.client is None:
            raise LLMError("No LLM configured. Set ANTHROPIC_API_KEY in .env.")

        output_config = dict(params.pop("output_config", None) or {})
        output_config.setdefault("effort", effort or self.settings.effort)
        params.setdefault("max_tokens", 16000)
        params["model"] = self.settings.model
        params["output_config"] = output_config

        try:
            if self.settings.refusal_fallback:
                response = self.client.beta.messages.create(
                    betas=[FALLBACK_BETA], fallbacks="default", **params
                )
            else:
                response = self.client.messages.create(**params)
        except anthropic.AuthenticationError as e:
            raise LLMError("The API key in .env was rejected.") from e
        except anthropic.PermissionDeniedError as e:
            raise LLMError("The API key does not have access to this model or endpoint.") from e
        except anthropic.NotFoundError as e:
            raise LLMError(
                f"Model '{self.settings.model}' or the endpoint was not found. "
                "Check TT_MODEL and ANTHROPIC_BASE_URL in .env."
            ) from e
        except anthropic.RateLimitError as e:
            raise LLMError("Rate limited by the LLM endpoint. Try again shortly.") from e
        except anthropic.BadRequestError as e:
            hint = " If you use a gateway, try TT_REFUSAL_FALLBACK=false." if self.settings.base_url else ""
            raise LLMError(f"The LLM endpoint rejected the request: {e.message}.{hint}") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"LLM endpoint error ({e.status_code}): {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"Could not reach {self.settings.base_url or 'api.anthropic.com'}.") from e

        if response.stop_reason == "refusal":
            raise LLMError("The model declined this request.")
        return response

    def json(self, *, system: str, prompt: str, schema: dict, effort: str = "low") -> dict:
        """Ask for a JSON object that matches `schema` (structured outputs)."""
        response = self.create(
            effort=effort,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        if response.stop_reason == "max_tokens":
            raise LLMError("The model ran out of output tokens before finishing its answer.")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError("The model did not return valid JSON.") from e


def text_of(response) -> str:
    return "\n".join(b.text for b in response.content if b.type == "text").strip()


def echo_content(content: list) -> list:
    """Assistant content to send back on the next turn.

    After a mid-output refusal fallback, thinking and tool_use blocks that appear before the
    last `fallback` block belong to the declined attempt and must not be echoed back.
    """
    last = max((i for i, b in enumerate(content) if b.type == "fallback"), default=-1)
    drop = {"thinking", "redacted_thinking", "tool_use"}
    return [b for i, b in enumerate(content) if not (i < last and b.type in drop)]
