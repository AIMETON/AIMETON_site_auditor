from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.analysis_async_api import (
    AnalysisNotFoundError,
    create_analysis_runtime,
    get_analysis_events_payload,
    get_analysis_status_payload,
    schedule_analysis_runtime,
)
from app.company_intelligence import run_company_intelligence
from app.discovery import run_hunt
from app.heuristics import heuristic_analysis
from app.external_sources import run_enriched_site_analysis
from app.mcp_security import browser_mcp_origins
from app.mission_orchestrator import (
    EntryPoint,
    default_site_mission_request,
    get_mission_orchestrator,
    record_legacy_site_turn,
)
from app.models import CompanyIntelligenceRequest, HuntRequest
from app.runtime_convergence import runtime_convergence_snapshot
from app.runtime_time import runtime_time_snapshot
from app.scraper import fetch_site
from app.temporal_mcp_tools import deadline_check_payload, wait_status_payload


def _additional_allowlist_values(variable_name: str) -> list[str]:
    """Read a comma-separated operator allowlist without accepting wildcards."""
    values: list[str] = []
    for raw_value in os.getenv(variable_name, "").split(","):
        value = raw_value.strip()
        if value and "*" not in value:
            values.append(value)
    return values


_DEFAULT_ALLOWED_HOSTS = [
    "auditor.aimeton.ru",
    "auditor.aimeton.ru:443",
    "stage-auditor.aimeton.ru",
    "stage-auditor.aimeton.ru:443",
    "git-hub-site-auditor.replit.app",
    "git-hub-site-auditor.replit.app:443",
    "localhost",
    "localhost:8000",
    "127.0.0.1",
    "127.0.0.1:8000",
]

_DEFAULT_ALLOWED_ORIGINS = [
    "https://auditor.aimeton.ru",
    "https://stage-auditor.aimeton.ru",
    "https://git-hub-site-auditor.replit.app",
]

MCP_TRANSPORT_SECURITY = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=[
        *_DEFAULT_ALLOWED_HOSTS,
        *_additional_allowlist_values("AIMETON_MCP_ALLOWED_HOSTS"),
    ],
    allowed_origins=[
        *_DEFAULT_ALLOWED_ORIGINS,
        *browser_mcp_origins(),
        *_additional_allowlist_values("AIMETON_MCP_ALLOWED_ORIGINS"),
    ],
)

mcp = FastMCP(
    "AIMETON Site Auditor",
    instructions=(
        "Public economic intelligence profile: site analysis, asynchronous analysis missions, "
        "company hunting and source-traceable company intelligence. Unconfirmed mentions must "
        "not be presented as facts. Prefer analysis.start/status/events for browser MCP clients "
        "with short request timeouts; analyze_site remains available for backward compatibility. "
        "Check runtime.convergence before a long mission when deployment stability matters. "
        "When polling analysis.status or analysis.events repeatedly, increment the poll argument "
        "using next_poll from the previous response so clients that deduplicate identical tool "
        "calls still send each poll."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=MCP_TRANSPORT_SECURITY,
)

admin_mcp = FastMCP(
    "AIMETON Site Auditor Admin",
    instructions=(
        "Administrative profile. Access is restricted by bearer token at the ASGI boundary. "
        "Tools must return sanitized operational metadata only."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=MCP_TRANSPORT_SECURITY,
)


@mcp.tool()
async def analyze_site(url: str) -> dict:
    """Analyze one public website synchronously; retained for backward compatibility."""
    orchestrator = get_mission_orchestrator()
    mission = orchestrator.create_mission(
        default_site_mission_request(url),
        entry_point=EntryPoint.MCP,
    )
    try:
        page = await fetch_site(url)
    except Exception:
        record_legacy_site_turn(
            orchestrator,
            mission.contract.mission_id,
            final_url=url,
            succeeded=False,
        )
        raise
    try:
        result = await run_enriched_site_analysis(
            page["final_url"],
            page["title"],
            page["text"],
        )
    except Exception as exc:
        result = heuristic_analysis(page["final_url"], page["title"], page["text"])
        result.readiness.provider_states["routerai"] = (
            "not_configured"
            if isinstance(exc, RuntimeError)
            and "ROUTERAI_API_KEY" in str(exc)
            else "failed"
        )
        result.risks_and_assumptions.append(
            "Used fallback local analysis because the enriched audit path was unavailable."
        )
    record_legacy_site_turn(
        orchestrator,
        mission.contract.mission_id,
        final_url=page["final_url"],
        succeeded=True,
    )
    return result.model_copy(
        update={
            "mission_id": mission.contract.mission_id,
            "analysis_id": mission.contract.analysis_id,
        }
    ).model_dump(mode="json")


@mcp.tool(name="analysis.start")
async def analysis_start(url: str) -> dict:
    """Start a durable-in-process website analysis and return identifiers immediately."""
    started = create_analysis_runtime(url, entry_point=EntryPoint.MCP)
    schedule_analysis_runtime(
        source_url=url,
        mission_id=started.mission_id,
        analysis_id=started.analysis_id,
    )
    payload = started.model_dump(mode="json")
    payload["next_poll"] = 1
    payload["polling_instruction"] = (
        "Call analysis.status and/or analysis.events with poll=1, then use next_poll "
        "from each response for the next request."
    )
    return payload


@mcp.tool(name="analysis.status")
async def analysis_status(analysis_id: str, poll: int = 0) -> dict:
    """Read current state; increment poll on repeated calls to avoid client-side deduplication."""
    if poll < 0:
        raise ValueError("poll_must_be_non_negative")
    try:
        payload = get_analysis_status_payload(analysis_id)
    except AnalysisNotFoundError:
        raise ValueError("analysis_not_found") from None
    return {
        **payload,
        "poll": poll,
        "next_poll": poll + 1,
    }


@mcp.tool(name="analysis.events")
async def analysis_events(analysis_id: str, poll: int = 0) -> dict:
    """Read UMEL progress events; increment poll on repeated calls to avoid client-side deduplication."""
    if poll < 0:
        raise ValueError("poll_must_be_non_negative")
    try:
        events = get_analysis_events_payload(analysis_id)
    except AnalysisNotFoundError:
        raise ValueError("analysis_not_found") from None
    return {
        "analysis_id": analysis_id,
        "poll": poll,
        "next_poll": poll + 1,
        "event_count": len(events),
        "events": events,
    }


@mcp.tool()
async def start_search_mission(url: str) -> dict:
    """Create the same canonical search mission used by UI and REST adapters."""
    mission = get_mission_orchestrator().create_mission(
        default_site_mission_request(url),
        entry_point=EntryPoint.MCP,
    )
    return mission.model_dump(mode="json")


@mcp.tool(name="runtime.time")
async def runtime_time() -> dict:
    """Return the canonical read-only AIMETON UTC time and trust state."""
    return runtime_time_snapshot().model_dump(mode="json")


@mcp.tool(name="runtime.convergence")
async def runtime_convergence() -> dict:
    """Return restart-aware exact-SHA Stage convergence for long mission admission."""
    return runtime_convergence_snapshot()


@mcp.tool(name="runtime.wait.status")
async def runtime_wait_status(wait_id: str) -> dict:
    """Read one persisted temporal intent without changing its state."""
    return wait_status_payload(wait_id)


@mcp.tool(name="runtime.deadline.check")
async def runtime_deadline_check(wait_id: str) -> dict:
    """Evaluate one persisted temporal intent against fresh trusted AIMETON Time."""
    return deadline_check_payload(wait_id)


@mcp.tool()
async def hunt_companies(
    region: str,
    industries: list[str] | None = None,
    focus: list[str] | None = None,
    search_zone: str | None = None,
    output_limit: int = 10,
) -> dict:
    """Find and rank companies by territory, industry and commercial opportunity signals."""
    request = HuntRequest(
        region=region,
        search_zone=search_zone,
        industries=industries or [],
        focus=focus or [],
        output_limit=output_limit,
    )
    result = await run_hunt(request)
    return result.model_dump(mode="json")


@mcp.tool()
async def company_intelligence(
    company_name: str,
    url: str | None = None,
    region: str | None = None,
    max_sources: int = 20,
) -> dict:
    """Build a source-traceable company profile from public information."""
    request = CompanyIntelligenceRequest(
        company_name=company_name,
        url=url,
        region=region,
        max_sources=max_sources,
    )
    result = await run_company_intelligence(request)
    return result.model_dump(mode="json")


@admin_mcp.tool()
async def security_profile() -> dict:
    """Return sanitized MCP security configuration for operator verification."""
    return {
        "profile": "admin",
        "authentication": "bearer-token",
        "public_tools": [
            "analyze_site",
            "analysis.start",
            "analysis.status",
            "analysis.events",
            "start_search_mission",
            "runtime.time",
            "runtime.convergence",
            "runtime.wait.status",
            "runtime.deadline.check",
            "hunt_companies",
            "company_intelligence",
        ],
        "admin_tools": ["security_profile"],
        "rate_limit_enabled": True,
        "concurrency_limit_enabled": True,
        "audit_fields": ["actor", "profile", "request_id", "timestamp", "result", "duration_ms"],
        "secret_values_exposed": False,
    }


mcp_http_app = mcp.streamable_http_app()
admin_mcp_http_app = admin_mcp.streamable_http_app()
