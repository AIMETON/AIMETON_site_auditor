from contextlib import asynccontextmanager
import os
from pathlib import Path
import re

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response


class NoCacheStaticFiles(StaticFiles):
    """Serve static assets with cache disabled so browsers always fetch the latest JS/CSS."""

    def file_response(self, *args, **kwargs) -> Response:
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp


from app.auth_api import router as auth_router
from app.company_intelligence_runtime import run_company_intelligence
from app.discovery import run_hunt
from app.entity_resolution.api import router as entity_resolution_router
from app.evidence_crawler.api import router as evidence_crawler_router
from app.identity_evidence.api import router as identity_evidence_router
from app.external_sources import run_enriched_site_analysis
from app.hunter_handbook import handbook
from app.hunter_async_runtime import get_hunter_run, start_hunter_run, stop_hunter_run
from app.hunter_settings import get_hunter_settings_repository
from app.search_strategy_settings import get_search_strategy_settings_repository
from app.hunter_sources import get_hunter_sources
from app.llm import chat_with_routerai
from app.llm_runtime_settings import get_llm_runtime_settings_repository
from app.audit_dialogue import run_audit_dialogue
from app.research_control import authorize_research, bind_research, bind_settings_snapshot
from app.research_execution import run_controlled, ResearchInterrupted
from app.research_settings_api import router as research_settings_router
from app.mcp_security import McpSecurityMiddleware
from app.mcp_server import admin_mcp, admin_mcp_http_app, mcp, mcp_http_app
from app.mission_orchestrator import (
    EntryPoint,
    default_site_mission_request,
    get_mission_orchestrator,
    record_legacy_site_turn,
)
from app.mission_orchestrator.api import router as mission_router
from app.models import (
    AnalyzeRequest,
    ChatRequest,
    CompanyIntelligenceRequest,
    HuntCandidate,
    HuntFunnel,
    HuntRequest,
    SiteAnalysis,
)
from app.osint_tools import get_osint_tools
from app.retention_runtime import build_retention_runner
from app.runtime_core.api import router as runtime_router
from app.scraper import FetchError, fetch_site
from app.search_gateway import get_search_gateway, search_policy_from_env
from app.search_gap_shadow_refinement import build_shadow_follow_up_queries
from app.search_gap_shadow_trace import persist_shadow_follow_up_suggestions
from app.search_regime_shadow import resolve_auto_search_regime
from app.sef.company_profile import (
    CompanyProfileBuildRequest,
    CompanyProfileV1,
    build_company_profile_from_request,
)
from app.sef.exports import (
    MARKDOWN_MEDIA_TYPE,
    WORD_MEDIA_TYPE,
    export_filename,
    render_report_docx,
    render_report_markdown,
    render_site_analysis_docx,
    render_site_analysis_markdown,
)
from app.sef.report import (
    HumanReviewedReportV1,
    ReportBuildRequest,
    ReportReleaseError,
    ReportReviewPackage,
    ReportReviewPackageRequest,
    build_human_reviewed_report,
    build_review_package_from_request,
    render_report_html,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    runtime_db = os.getenv("AIMETON_RUNTIME_DB", "data/runtime-core.sqlite3")
    get_search_strategy_settings_repository().ensure_bootstrap_default()
    get_llm_runtime_settings_repository().ensure_bootstrap_default()
    retention_runner = build_retention_runner(runtime_db)
    _app.state.retention_runner = retention_runner
    await retention_runner.start()
    try:
        async with mcp.session_manager.run(), admin_mcp.session_manager.run():
            yield
    finally:
        await retention_runner.stop()


app = FastAPI(
    title="AIMETON Site Auditor",
    version="0.16.3",
    lifespan=lifespan,
)
app.include_router(auth_router)
app.include_router(research_settings_router)
app.include_router(runtime_router)
app.include_router(mission_router)
app.include_router(evidence_crawler_router)
app.include_router(entity_resolution_router)
app.include_router(identity_evidence_router)


@app.middleware("http")
async def canonical_mcp_path(request: Request, call_next):
    """Return explicit relative Location headers independent of proxy scheme rewriting."""
    if request.url.path == "/mcp":
        return Response(status_code=307, headers={"Location": "/mcp/"})
    if request.url.path == "/mcp-admin":
        return Response(status_code=307, headers={"Location": "/mcp-admin/"})
    return await call_next(request)


app.mount("/static", NoCacheStaticFiles(directory="static"), name="static")
app.mount("/mcp", McpSecurityMiddleware(mcp_http_app, admin=False), name="mcp")
app.mount("/mcp-admin", McpSecurityMiddleware(admin_mcp_http_app, admin=True), name="mcp-admin")


@app.get("/")
def index():
    return FileResponse(
        Path("static/index.html"),
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


AI_API_DOCS = """AIMETON Site Auditor API — AI-readable discovery\n\nThis document requires no JavaScript and is suitable for sandboxed AI agents, curl/wget and text-only HTTP clients.\n\nDISCOVERY\nGET /llms.txt           shortest AI-readable entrypoint\nGET /api/capabilities   compact JSON capability/index document\nGET /api/docs.txt       this plain-text guide\nGET /openapi.json       authoritative complete OpenAPI schema\nGET /api/health         runtime health plus discovery links\nGET /docs               Swagger UI for humans; JavaScript may be required\n\nRECOMMENDED AGENT FLOW\n1. Read /llms.txt or /api/capabilities.\n2. Fetch /openapi.json for all routes, request/response schemas and operation details.\n3. If OpenAPI cannot be parsed, use /api/docs.txt for orientation.\n4. Use /api/health for deployment/runtime status only.\n\nCORE API FAMILIES\n- analysis: /api/analyze, /api/company-intelligence, /api/hunt, /api/chat\n- missions/orchestration: /api/missions/...\n- runtime: /api/runtime/...\n- search: /api/search/health\n- identity/evidence: mission-scoped crawler, resolution and evidence routes\n- SEF/reporting: /api/sef/company-profile, /api/sef/report and report exports\n- preliminary exports: /api/export/analysis.md and /api/export/analysis.docx\n- MCP: /mcp/ ; administrative MCP: /mcp-admin/\n\nThe authoritative API contract is always /openapi.json.\n"""


@app.get("/llms.txt", include_in_schema=False)
def llms_txt():
    return Response(AI_API_DOCS, media_type="text/plain; charset=utf-8")


@app.get("/api/docs.txt", include_in_schema=False)
def api_docs_text():
    return Response(AI_API_DOCS, media_type="text/plain; charset=utf-8")


@app.get("/api/capabilities", tags=["Discovery"], summary="Machine-readable API discovery index")
def api_capabilities():
    return {
        "service": "AIMETON Site Auditor",
        "version": app.version,
        "purpose": "AI-readable API discovery without browser JavaScript",
        "openapi_json": "/openapi.json",
        "plain_text_docs": "/api/docs.txt",
        "llms_txt": "/llms.txt",
        "swagger_ui": "/docs",
        "health": "/api/health",
        "mcp": "/mcp/",
        "mcp_admin": "/mcp-admin/",
        "javascript_required": False,
        "authoritative_contract": "/openapi.json",
        "recommended_flow": [
            "/api/capabilities",
            "/openapi.json",
            "/api/docs.txt",
            "/api/health",
        ],
    }


def deployment_sha() -> str | None:
    value = os.getenv("AIMETON_DEPLOY_SHA", "").strip()
    if not value:
        path = Path(".aimeton-deploy-sha")
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
    return value if re.fullmatch(r"[0-9a-f]{40}", value) else None


@app.get("/api/health")
def health():
    identity = deployment_sha()
    payload = {
        "status": "ok",
        "version": app.version,
        "analysis_mode": "ai-sales-with-canonical-km-company-profile",
        "osint": "contacts-finance-workforce-legal-ownership",
        "api": "/docs",
        "openapi": "/openapi.json",
        "capabilities": "/api/capabilities",
        "api_docs_text": "/api/docs.txt",
        "llms_txt": "/llms.txt",
        "auth": "/api/auth",
        "mcp": "/mcp",
        "mcp_admin": "/mcp-admin",
        "mcp_security": "public-rate-limited-admin-authenticated",
        "runtime_core": "/api/runtime",
        "mission_orchestrator": "/api/missions",
        "bootstrap_crawler": "/api/missions/{mission_id}/bootstrap-crawl",
        "entity_resolution": "/api/missions/{mission_id}/resolve-identity",
        "identity_history": "/api/missions/{mission_id}/identity-history",
        "identity_search": "/api/missions/{mission_id}/identity-search",
        "identity_evidence": "/api/missions/{mission_id}/identity-evidence",
        "search_gateway": "/api/search/health",
        "sef_company_profile": "/api/sef/company-profile",
        "sef_report_review_package": "/api/sef/report/review-package",
        "sef_report": "/api/sef/report",
        "sef_report_markdown": "/api/sef/report.md",
        "sef_report_word": "/api/sef/report.docx",
        "preliminary_analysis_markdown": "/api/export/analysis.md",
        "preliminary_analysis_word": "/api/export/analysis.docx",
    }
    if identity:
        payload["deployment_sha"] = identity
    return payload


@app.get("/api/search/health")
def search_health():
    providers = [
        item.model_dump(mode="json", exclude_none=True)
        for item in get_search_gateway().health(search_policy_from_env())
    ]
    active = [item for item in providers if item["state"] == "active"]
    return {
        "status": "ok" if active else "degraded",
        "providers": providers,
        "active_providers": [item["provider"] for item in active],
        "secrets_exposed": False,
    }


@app.get("/api/hunter-handbook")
def hunter_handbook():
    return handbook()


@app.get("/api/hunter-sources")
def hunter_sources():
    return get_hunter_sources()


@app.get("/api/osint-tools")
def osint_tools():
    return get_osint_tools()


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest, request: Request):
    """Find an AI sales opportunity and enrich it with a source-traceable company and canonical KM profile."""
    control = authorize_research(req, request)
    orchestrator = get_mission_orchestrator()
    mission = orchestrator.create_mission(
        default_site_mission_request(str(req.url)),
        entry_point=EntryPoint.LEGACY_ADAPTER,
    )
    bind_settings_snapshot(control, mission.contract.mission_id, mission.contract.analysis_id)
    final_url = str(req.url)
    async def operation():
        nonlocal final_url
        page = await fetch_site(str(req.url))
        final_url = page["final_url"]
        return await run_enriched_site_analysis(page["final_url"], page["title"], page["text"])
    try:
        result = await run_controlled(control, operation)
        record_legacy_site_turn(orchestrator, mission.contract.mission_id, final_url=final_url, succeeded=True)
        return result.model_copy(update={"mission_id": mission.contract.mission_id, "analysis_id": mission.contract.analysis_id})
    except (ResearchInterrupted, TimeoutError) as exc:
        record_legacy_site_turn(orchestrator, mission.contract.mission_id, final_url=final_url, succeeded=False)
        raise HTTPException(status_code=408, detail=getattr(exc, "reason", "operation_timeout")) from exc
    except (FetchError, httpx.HTTPError, ValueError) as exc:
        record_legacy_site_turn(orchestrator, mission.contract.mission_id, final_url=final_url, succeeded=False)
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/company-intelligence")
async def company_intelligence(req: CompanyIntelligenceRequest, request: Request = None):
    control = authorize_research(req, request, service="company-intelligence")
    if control and control.settings:
        # Explicit transitional scope; this is not a canonical orchestrator mission.
        bind_settings_snapshot(control, "company-run:" + control.run_id, control.run_id)
    try:
        result = await run_controlled(control, lambda: run_company_intelligence(req))
        if control and control.settings:
            return {**result.model_dump(mode="json"), "research": control.snapshot()}
        return result
    except (ResearchInterrupted, TimeoutError) as exc:
        raise HTTPException(status_code=408, detail=getattr(exc, "reason", "operation_timeout")) from exc
    except (FetchError, httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(
    "/api/sef/company-profile",
    response_model=CompanyProfileV1,
)
def sef_company_profile(req: CompanyProfileBuildRequest):
    try:
        return build_company_profile_from_request(req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(
    "/api/sef/report/review-package",
    response_model=ReportReviewPackage,
)
def sef_report_review_package(req: ReportReviewPackageRequest):
    try:
        return build_review_package_from_request(req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _build_report_or_http_error(
    req: ReportBuildRequest,
) -> HumanReviewedReportV1:
    try:
        return build_human_reviewed_report(req)
    except ReportReleaseError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "report_release_blocked",
                "blockers": exc.blockers,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(
    "/api/sef/report",
    response_model=HumanReviewedReportV1,
)
def sef_report(req: ReportBuildRequest):
    return _build_report_or_http_error(req)


@app.post(
    "/api/sef/report.html",
    response_class=HTMLResponse,
)
def sef_report_html(req: ReportBuildRequest):
    return HTMLResponse(render_report_html(_build_report_or_http_error(req)))


def _export_headers(report_id: str, extension: str) -> dict[str, str]:
    return {
        "Content-Disposition": (
            f'attachment; filename="{export_filename(report_id, extension)}"'
        ),
        "X-AIMETON-Report-ID": report_id,
    }


@app.post("/api/sef/report.md")
def sef_report_markdown(req: ReportBuildRequest):
    report = _build_report_or_http_error(req)
    headers = _export_headers(report.id, "md")
    headers["X-AIMETON-Report-Digest"] = report.integrity.report_content_digest
    return Response(
        render_report_markdown(report),
        media_type=MARKDOWN_MEDIA_TYPE,
        headers=headers,
    )


@app.post("/api/sef/report.docx")
def sef_report_docx(req: ReportBuildRequest):
    report = _build_report_or_http_error(req)
    headers = _export_headers(report.id, "docx")
    headers["X-AIMETON-Report-Digest"] = report.integrity.report_content_digest
    return Response(
        render_report_docx(report),
        media_type=WORD_MEDIA_TYPE,
        headers=headers,
    )


@app.post("/api/export/analysis.md")
def preliminary_analysis_markdown(req: SiteAnalysis):
    return Response(
        render_site_analysis_markdown(req),
        media_type=MARKDOWN_MEDIA_TYPE,
        headers=_export_headers("preliminary-analysis", "md"),
    )


@app.post("/api/export/analysis.docx")
def preliminary_analysis_docx(req: SiteAnalysis):
    return Response(
        render_site_analysis_docx(req),
        media_type=WORD_MEDIA_TYPE,
        headers=_export_headers("preliminary-analysis", "docx"),
    )


async def _run_hunt_payload(
    req: HuntRequest,
    requested_regime: str,
    progress_callback=None,
):
    allowed_regimes = {"auto", "precision", "balanced", "discovery"}
    if requested_regime not in allowed_regimes:
        raise HTTPException(status_code=422, detail="invalid_search_regime")

    effective = get_hunter_settings_repository().apply(req)
    if progress_callback is None:
        result = await run_hunt(effective)
    else:
        result = await run_hunt(effective, on_progress=progress_callback)
    payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
    if requested_regime == "auto":
        funnel = payload.get("funnel")
        if isinstance(funnel, dict):
            decision = resolve_auto_search_regime(
                raw_results=int(funnel.get("raw_results") or 0),
                unique_candidates=int(funnel.get("unique_candidates") or 0),
                qualified_candidates=int(funnel.get("qualified_candidates") or 0),
                duplicate_results=int(funnel.get("duplicate_results") or 0),
                excluded_results=int(funnel.get("excluded_results") or 0),
            )
            effective_regime = decision.effective
            regime_reason = decision.reason
        else:
            effective_regime = "balanced"
            regime_reason = "auto_balanced_default"
    else:
        effective_regime = requested_regime
        regime_reason = "user_override"

    payload["search_regime"] = {
        "requested": requested_regime,
        "effective": effective_regime,
        "reason": regime_reason,
        "routing_changed": False,
        "steering_enabled": False,
    }

    refinement_funnel = (
        result.funnel
        if hasattr(result, "funnel")
        else HuntFunnel.model_validate(payload.get("funnel") or {})
    )
    executed_queries = (
        list(result.queries)
        if hasattr(result, "queries")
        else [str(item) for item in (payload.get("queries") or [])]
    )
    source_candidates = (
        list(result.candidates)
        if hasattr(result, "candidates")
        else list(payload.get("candidates") or [])
    )
    refinement_candidates: list[HuntCandidate] = []
    for item in source_candidates:
        if isinstance(item, HuntCandidate):
            refinement_candidates.append(item)
            continue
        try:
            refinement_candidates.append(HuntCandidate.model_validate(item))
        except (TypeError, ValueError):
            continue

    refinement = build_shadow_follow_up_queries(
        req=effective,
        funnel=refinement_funnel,
        executed_queries=executed_queries,
        effective_regime=effective_regime,
        candidates=refinement_candidates,
    )
    payload["search_refinement_shadow"] = {
        "gap_count": len(refinement.gaps),
        "gaps": [
            {"code": gap.code, "evidence_target": gap.evidence_target, "reason": gap.reason}
            for gap in refinement.gaps
        ],
        "suggestion_count": len(refinement.suggestions),
        "suggestions": [
            {
                "query": item.query,
                "reason_code": item.reason_code,
                "evidence_target": item.evidence_target,
            }
            for item in refinement.suggestions
        ],
        "routing_changed": False,
        "steering_enabled": False,
    }
    trace_mission_id = getattr(result, "trace_mission_id", None)
    trace_attempt_id = getattr(result, "trace_attempt_id", None)
    if trace_mission_id and trace_attempt_id:
        persist_shadow_follow_up_suggestions(
            mission_id=trace_mission_id,
            attempt_id=trace_attempt_id,
            effective_regime=effective_regime,
            plan=refinement,
        )
    return payload


@app.post("/api/hunt")
async def hunt(req: HuntRequest, request: Request):
    requested_regime = request.query_params.get("search_regime", "auto").strip().lower()
    return await _run_hunt_payload(req, requested_regime)


@app.post("/api/hunt/start", status_code=status.HTTP_202_ACCEPTED)
async def start_hunt(req: HuntRequest, request: Request):
    requested_regime = request.query_params.get("search_regime", "auto").strip().lower()
    if requested_regime not in {"auto", "precision", "balanced", "discovery"}:
        raise HTTPException(status_code=422, detail="invalid_search_regime")

    async def runner(progress_callback):
        return await _run_hunt_payload(req, requested_regime, progress_callback)

    return start_hunter_run(
        region=req.region,
        search_zone=req.search_zone,
        requested_regime=requested_regime,
        runner=runner,
    )


@app.get("/api/hunt/runs/{run_id}")
async def hunt_status(
    run_id: str,
    x_hunter_run_token: str = Header(default="", alias="X-Hunter-Run-Token"),
):
    try:
        return get_hunter_run(run_id, x_hunter_run_token)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="hunter_run_not_found") from exc


@app.post("/api/hunt/runs/{run_id}/stop")
async def stop_hunt(
    run_id: str,
    x_hunter_run_token: str = Header(default="", alias="X-Hunter-Run-Token"),
):
    try:
        return stop_hunter_run(run_id, x_hunter_run_token)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="hunter_run_not_found") from exc


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    if req.research_settings_revision is not None:
        raise HTTPException(status_code=409, detail="settings_not_supported_for_chat")
    control = authorize_research(req, request, use_saved_settings=False)
    try:
        with bind_research(control):
            return await run_audit_dialogue(req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
