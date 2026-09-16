from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "static"


def test_live_analysis_exposes_workspace_event_bridge_without_second_poll_owner() -> None:
    script = (STATIC / "live-analysis.js").read_text(encoding="utf-8")

    for event_name in (
        "aimeton:analysis-started",
        "aimeton:analysis-update",
        "aimeton:analysis-complete",
    ):
        assert event_name in script

    assert "new CustomEvent" in script
    assert "status_url" in script
    assert "events_url" in script
    assert "setInterval(poll, 1200)" in script


def test_workspace_bridge_keeps_backend_states_and_real_event_payload() -> None:
    script = (STATIC / "live-analysis.js").read_text(encoding="utf-8")

    for state in ("completed", "degraded", "blocked", "failed"):
        assert state in script

    assert "events: latestEvents.map" in script
    assert "result: availableResult" in script
    assert "terminal: TERMINAL.has(state)" in script
    assert "percentage" not in script.lower()
    assert "progress_percent" not in script.lower()
