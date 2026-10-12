"""Optional, replaceable LLM providers.

Agents are fully functional without an LLM: detection, documentation,
root-cause ranking and notification policy are deterministic. When a provider
is configured, it only adds a *narrative* that is validated before storage and
always labelled as AI-generated.

Providers (LLM_PROVIDER):
* none              - default; no network calls.
* anthropic         - Claude via the official `anthropic` SDK (LLM_API_KEY or the
                      SDK's own credential resolution). Default model claude-opus-5-5.
* openai_compatible - any server exposing POST {LLM_BASE_URL}/chat/completions
                      (e.g. a local Ollama/vLLM). Requires LLM_BASE_URL and LLM_MODEL.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

import httpx

from ..config import settings

log = logging.getLogger("ghost.llm")


class LLMUnavailable(Exception):
    """Provider not configured, unreachable, rate limited, timed out or refused."""


@dataclass
class LLMResult:
    data: dict
    model: str
    latency_ms: int


class LLMProvider:
    name = "none"

    @property
    def model(self) -> str | None:
        return None

    def available(self) -> bool:
        return False

    def generate_json(self, system: str, prompt: str, schema: dict, max_tokens: int = 4000) -> LLMResult:
        raise LLMUnavailable("no LLM provider configured")


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self) -> None:
        try:
            import anthropic  # noqa: F401
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise LLMUnavailable("anthropic SDK not installed (pip install anthropic)") from exc
        import anthropic

        kwargs = {"timeout": settings.llm_timeout_s, "max_retries": 2}
        if settings.llm_api_key:
            kwargs["api_key"] = settings.llm_api_key
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(**kwargs)

    @property
    def model(self) -> str:
        return settings.llm_model or "claude-opus-5-5"

    def available(self) -> bool:
        return True

    def generate_json(self, system: str, prompt: str, schema: dict, max_tokens: int = 4000) -> LLMResult:
        a = self._anthropic
        started = time.time()
        try:
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
                # Server-side refusal fallback (routes by refusal category).
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except a.RateLimitError as exc:
            raise LLMUnavailable(f"rate limited (request id {getattr(exc, 'request_id', None)})") from exc
        except a.APITimeoutError as exc:
            raise LLMUnavailable("timeout") from exc
        except a.APIStatusError as exc:
            raise LLMUnavailable(f"API error {exc.status_code}") from exc
        except a.APIConnectionError as exc:
            raise LLMUnavailable("connection error") from exc
        if response.stop_reason == "refusal":
            raise LLMUnavailable("model refused the request")
        if response.stop_reason == "max_tokens":
            raise LLMUnavailable("output truncated (max_tokens)")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMUnavailable("non-JSON output") from exc
        return LLMResult(data=data, model=response.model, latency_ms=int((time.time() - started) * 1000))


class OpenAICompatibleProvider(LLMProvider):
    name = "openai_compatible"

    @property
    def model(self) -> str | None:
        return settings.llm_model

    def available(self) -> bool:
        return bool(settings.llm_base_url and settings.llm_model)

    def generate_json(self, system: str, prompt: str, schema: dict, max_tokens: int = 4000) -> LLMResult:
        if not self.available():
            raise LLMUnavailable("LLM_BASE_URL and LLM_MODEL are required")
        headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
        body = {
            "model": settings.llm_model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system + "\nReply with a single JSON object matching this schema:\n" + json.dumps(schema)},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        started = time.time()
        try:
            r = httpx.post(settings.llm_base_url.rstrip("/") + "/chat/completions", json=body, headers=headers,
                           timeout=settings.llm_timeout_s)
        except httpx.TimeoutException as exc:
            raise LLMUnavailable("timeout") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailable("connection error") from exc
        if r.status_code == 429:
            raise LLMUnavailable("rate limited")
        if r.status_code >= 400:
            raise LLMUnavailable(f"API error {r.status_code}")
        try:
            text = r.json()["choices"][0]["message"]["content"]
            data = json.loads(text)
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMUnavailable("unparseable output") from exc
        return LLMResult(data=data, model=settings.llm_model, latency_ms=int((time.time() - started) * 1000))


_provider: LLMProvider | None = None


def get_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        try:
            if settings.llm_provider == "anthropic":
                _provider = AnthropicProvider()
            elif settings.llm_provider == "openai_compatible":
                _provider = OpenAICompatibleProvider()
            else:
                _provider = LLMProvider()
        except LLMUnavailable as exc:
            log.warning("LLM provider disabled: %s", exc)
            _provider = LLMProvider()
    return _provider


def set_provider(provider: LLMProvider) -> None:
    """Test hook."""
    global _provider
    _provider = provider


def status() -> dict:
    p = get_provider()
    return {"provider": p.name, "configured": p.available(), "model": p.model}
