from __future__ import annotations

import os


_FALSE_VALUES = {"0", "false", "no", "off"}


def compiled_two_call_enabled() -> bool:
    """Enable the compiled two-call analytical path with one-switch rollback."""
    return os.getenv("AIMETON_COMPILED_TWO_CALL", "1").strip().lower() not in _FALSE_VALUES


def minimal_llm_routing_enabled() -> bool:
    """Keep discovery/triage deterministic when the compiled deep path is active."""
    return os.getenv("AIMETON_MINIMAL_LLM_ROUTING", "1").strip().lower() not in _FALSE_VALUES
