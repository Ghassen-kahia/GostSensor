"""Runtime configuration.

All settings come from environment variables (optionally loaded from a `.env`
file next to the repository root or in `backend/`). Secrets are never given
defaults here; features that need them degrade gracefully when they are absent.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


def _load_dotenv() -> None:
    for candidate in (REPO_DIR / ".env", BACKEND_DIR / ".env"):
        if not candidate.exists():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


_load_dotenv()


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> list[str]:
    value = _env(name, "") or ""
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    # Paths
    source_dir: Path = field(default_factory=lambda: Path(_env("GHOST_SOURCE_DIR", str(REPO_DIR))))
    data_dir: Path = field(default_factory=lambda: Path(_env("GHOST_DATA_DIR", str(REPO_DIR / "data"))))

    # API
    host: str = field(default_factory=lambda: _env("GHOST_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("GHOST_PORT", "8000")))
    cors_origins: list[str] = field(
        default_factory=lambda: _env_list("GHOST_CORS_ORIGINS") or ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    dashboard_url: str = field(default_factory=lambda: _env("GHOST_DASHBOARD_URL", "http://localhost:5173"))
    operator_token: str | None = field(default_factory=lambda: _env("GHOST_OPERATOR_TOKEN"))
    automation_token: str | None = field(default_factory=lambda: _env("GHOST_AUTOMATION_TOKEN"))
    allow_anonymous_read: bool = field(default_factory=lambda: _env_bool("GHOST_ALLOW_ANONYMOUS_READ", True))
    max_body_bytes: int = field(default_factory=lambda: int(_env("GHOST_MAX_BODY_BYTES", str(512 * 1024))))

    # Replay clock
    data_mode: str = field(default_factory=lambda: (_env("GHOST_DATA_MODE", "REPLAY") or "REPLAY").upper())
    replay_start: str = field(default_factory=lambda: _env("GHOST_REPLAY_START", "2026-03-02T06:00:00Z"))
    replay_speed: float = field(default_factory=lambda: float(_env("GHOST_REPLAY_SPEED", "60")))
    tick_seconds: float = field(default_factory=lambda: float(_env("GHOST_TICK_SECONDS", "5")))
    background_engine: bool = field(default_factory=lambda: _env_bool("GHOST_BACKGROUND_ENGINE", True))

    # Orchestration
    orchestration_mode: str = field(default_factory=lambda: (_env("GHOST_ORCHESTRATION_MODE", "n8n") or "n8n").lower())
    n8n_base_url: str | None = field(default_factory=lambda: _env("N8N_BASE_URL", "http://localhost:5678"))
    n8n_api_key: str | None = field(default_factory=lambda: _env("N8N_API_KEY"))
    n8n_trigger_webhook: str | None = field(default_factory=lambda: _env("N8N_TRIGGER_WEBHOOK_URL"))
    # If n8n has not processed an incident within this many *real* seconds, the
    # backend runs the remaining agents itself so incidents are never stranded.
    local_fallback_after_s: int = field(default_factory=lambda: int(_env("GHOST_LOCAL_FALLBACK_AFTER_S", "300")))

    # LLM (optional; agents work deterministically without it)
    llm_provider: str = field(default_factory=lambda: (_env("LLM_PROVIDER", "none") or "none").lower())
    llm_model: str | None = field(default_factory=lambda: _env("LLM_MODEL"))
    llm_api_key: str | None = field(default_factory=lambda: _env("LLM_API_KEY"))
    llm_base_url: str | None = field(default_factory=lambda: _env("LLM_BASE_URL"))
    llm_timeout_s: float = field(default_factory=lambda: float(_env("LLM_TIMEOUT_S", "45")))

    # Email
    email_mode: str = field(default_factory=lambda: (_env("EMAIL_MODE", "test") or "test").lower())  # test | smtp
    smtp_host: str | None = field(default_factory=lambda: _env("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: int(_env("SMTP_PORT", "587")))
    smtp_user: str | None = field(default_factory=lambda: _env("SMTP_USER"))
    smtp_password: str | None = field(default_factory=lambda: _env("SMTP_PASSWORD"))
    smtp_starttls: bool = field(default_factory=lambda: _env_bool("SMTP_STARTTLS", True))
    smtp_ssl: bool = field(default_factory=lambda: _env_bool("SMTP_SSL", False))
    email_from: str | None = field(default_factory=lambda: _env("EMAIL_FROM"))
    alert_recipients: list[str] = field(default_factory=lambda: _env_list("ALERT_RECIPIENTS"))
    escalation_recipients: list[str] = field(default_factory=lambda: _env_list("ESCALATION_RECIPIENTS"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ghost.db"

    @property
    def derived_dir(self) -> Path:
        return self.data_dir / "derived"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def outbox_dir(self) -> Path:
        return self.data_dir / "outbox"


def load_policy(name: str) -> dict:
    """Load a JSON rule/policy file from backend/config (overridable via GHOST_CONFIG_DIR)."""
    base = Path(_env("GHOST_CONFIG_DIR", str(BACKEND_DIR / "config")))
    return json.loads((base / f"{name}.json").read_text(encoding="utf-8"))


settings = Settings()
