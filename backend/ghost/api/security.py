"""Authentication / authorisation dependencies and basic abuse protection."""
from __future__ import annotations

import hmac
import threading
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request

from ..config import settings


def _match(given: str | None, expected: str | None) -> bool:
    return bool(given and expected and hmac.compare_digest(given.encode(), expected.encode()))


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def require_read(authorization: str | None = Header(default=None)) -> str:
    if settings.allow_anonymous_read:
        return "anonymous"
    if _match(_bearer(authorization), settings.operator_token):
        return "operator"
    raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"})


def require_operator(authorization: str | None = Header(default=None), x_operator_name: str | None = Header(default=None)) -> str:
    if not settings.operator_token:
        raise HTTPException(503, "operator actions disabled: GHOST_OPERATOR_TOKEN is not configured")
    if not _match(_bearer(authorization), settings.operator_token):
        raise HTTPException(401, "operator token required", headers={"WWW-Authenticate": "Bearer"})
    name = (x_operator_name or "operator").strip()[:60] or "operator"
    return f"OPERATOR:{''.join(c for c in name if c.isalnum() or c in ' ._-@')}"


def require_automation(x_ghost_token: str | None = Header(default=None)) -> str:
    if not settings.automation_token:
        raise HTTPException(503, "automation endpoints disabled: GHOST_AUTOMATION_TOKEN is not configured")
    if not _match(x_ghost_token, settings.automation_token):
        raise HTTPException(401, "invalid automation token")
    return "n8n"


class RateLimiter:
    """Sliding-window limiter for mutating requests, keyed by client address."""

    def __init__(self, limit: int = 120, window_s: int = 60):
        self.limit, self.window = limit, window_s
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, request: Request) -> None:
        key = request.client.host if request.client else "unknown"
        now = time.time()
        with self.lock:
            q = self.hits[key]
            while q and q[0] < now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                raise HTTPException(429, "too many requests")
            q.append(now)


limiter = RateLimiter()
