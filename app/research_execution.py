"""Execution of the supported subset of an immutable user settings revision."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from functools import wraps

from app.research_settings import ResearchSettings


class PolicyUnavailable(ValueError):
    pass


class ResearchInterrupted(asyncio.CancelledError):
    """Cannot be swallowed by analytical fallback handlers catching Exception."""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def compile_policy(settings: ResearchSettings) -> dict:
    unsupported = [name for name in ("cost_warning_amount", "cost_limit_amount", "token_warning", "token_limit")
                   if getattr(settings, name) is not None]
    if unsupported:
        raise PolicyUnavailable("budget_enforcement_unavailable:" + ",".join(unsupported))
    if settings.unknown_price_action != "allow_unpriced":
        raise PolicyUnavailable("price_unknown:explicit_allow_unpriced_required")
    if settings.mission_timeout_seconds is not None and settings.hard_limit_action != "stop":
        raise PolicyUnavailable("deadline_pause_resume_unavailable:select_stop")
    return {name: getattr(settings, name) for name in (
        "mission_timeout_seconds", "request_timeout_seconds", "llm_call_timeout_seconds",
        "progress_warning_seconds", "retry_count", "retry_backoff_seconds", "hard_limit_action",
        "unknown_price_action")}


def active_settings() -> ResearchSettings | None:
    from app.research_control import current_research
    control = current_research()
    return control.settings if control else None


def operation_timeout(kind: str, legacy: float) -> float:
    settings = active_settings()
    if settings is None:
        return legacy
    return settings.llm_call_timeout_seconds if kind == "llm" else settings.request_timeout_seconds


def check_execution() -> None:
    from app.research_control import current_research
    control = current_research()
    if control and control.settings and control.stop_requested:
        raise ResearchInterrupted(control.stop_reason or "stopped_by_user")


def research_timed(kind: str):
    """Wall-clock bound, including redirects/body reads; legacy calls are unchanged."""
    def decorate(fn):
        @wraps(fn)
        async def wrapped(*args, **kwargs):
            if active_settings() is None:
                return await fn(*args, **kwargs)
            check_execution()
            timeout = operation_timeout(kind, 0)
            if "timeout_seconds" in kwargs:
                kwargs["timeout_seconds"] = timeout
            async with asyncio.timeout(timeout):
                return await fn(*args, **kwargs)
        return wrapped
    return decorate


async def run_controlled(control, operation):
    """The deadline spans acquisition and synthesis, not just the analytical tail."""
    from app.research_control import bind_research
    with bind_research(control):
        if control is None or control.settings is None:
            return await operation()
        check_execution()
        async def warn():
            await asyncio.sleep(control.settings.progress_warning_seconds)
            control.progress_warning = True
            control.checkpoint("progress_warning", control.snapshot())
        warning = asyncio.create_task(warn())
        deadline = asyncio.timeout(control.settings.mission_timeout_seconds)
        try:
            async with deadline:
                result = await operation()
                check_execution()
                return result
        except TimeoutError:
            if not deadline.expired():
                raise
            control.stop_requested = True
            control.stop_reason = "mission_timeout"
            control.checkpoint("stop", control.snapshot())
            raise ResearchInterrupted("mission_timeout") from None
        finally:
            warning.cancel()
            with suppress(asyncio.CancelledError):
                await warning
