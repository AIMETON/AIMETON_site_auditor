"""Draft preference API. Mount only with execution integration and user UI."""
import os

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.auth import User
from app.auth_api import CSRF_COOKIE, CSRF_HEADER, _require_csrf, current_user
from app.research_settings import ResearchSettings, ResearchSettingsRepository, Service, SettingsConflict, SettingsRecord


router = APIRouter(prefix="/api/user/research-settings", tags=["research-settings"])


def repository() -> ResearchSettingsRepository:
    return ResearchSettingsRepository(os.getenv("AIMETON_RUNTIME_DB", "data/runtime-core.sqlite3"))


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)
    settings: ResearchSettings


@router.get("/{service}", response_model=SettingsRecord)
def read_settings(service: Service, user: User = Depends(current_user),
                  store: ResearchSettingsRepository = Depends(repository)):
    return store.get(user.id, service)


@router.put("/{service}", response_model=SettingsRecord)
def save_settings(service: Service, payload: SettingsUpdate, user: User = Depends(current_user),
                  store: ResearchSettingsRepository = Depends(repository),
                  csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE),
                  csrf_header: str | None = Header(default=None, alias=CSRF_HEADER)):
    _require_csrf(csrf_cookie, csrf_header)
    try:
        return store.save(user.id, service, payload.settings, expected_revision=payload.expected_revision)
    except SettingsConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
