"""Whose Google account and settings the agent is working with right now.

The CLI uses the token.json account; the web scheduler wraps each user's run
in `use(RunContext(...))` so runs never share connections or cached values.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import time
from typing import Any, Callable

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


@dataclass
class RunContext:
    creds: Credentials
    work_start: time = time(9, 0)
    work_end: time = time(18, 0)
    work_days: tuple[int, ...] = (0, 1, 2, 3, 4)  # weekdays, 0 = Monday
    anytime: bool = False  # ignore work_start and work_end
    # The CLI has no signup, so it keeps these placeholders; web runs use the names from signup.
    first_name: str = "Jane"
    last_name: str = "Doe"
    # The user's labels, as labels.parse gives them; None (the CLI) reads labels.md from the repo.
    labels: dict[str, dict] | None = None
    _memo: dict[str, Any] = field(default_factory=dict)

    def cached(self, key: str, compute: Callable[[], Any]) -> Any:
        if key not in self._memo:
            self._memo[key] = compute()
        return self._memo[key]

    def service(self, name: str, version: str):
        return self.cached(f"service:{name}", lambda: build(name, version, credentials=self.creds))


_current: ContextVar[RunContext | None] = ContextVar("run_context", default=None)
_cli: RunContext | None = None


def current() -> RunContext:
    global _cli
    if ctx := _current.get():
        return ctx
    if _cli is None:
        import google_auth  # CLI only: may open a browser to log in

        _cli = RunContext(google_auth.credentials())
    return _cli


@contextmanager
def use(ctx: RunContext):
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)
