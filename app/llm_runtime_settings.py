from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
import os
from pathlib import Path
import sqlite3
from threading import RLock

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.search_observer_models import observer_profile


SETTINGS_KEY = "llm.runtime.settings.v1"


class LlmRole(StrEnum):
    FAST_RESEARCH = "fast_research"
    EXTRACTION = "extraction"
    REASONING = "reasoning"


class LlmOutputMode(StrEnum):
    STRICT_SCHEMA = "strict_schema"
    JSON_OBJECT = "json_object"


class LlmReasoningMode(StrEnum):
    OFF = "off"
    ON = "on"


class LlmReasoningEffort(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class LlmRoleSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_name: str = Field(min_length=1, max_length=80)
    model_id: str | None = Field(default=None, max_length=200)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(default=8192, ge=64, le=65536)
    timeout_seconds: float = Field(default=120.0, ge=2.0, le=600.0)
    output_mode: LlmOutputMode = LlmOutputMode.STRICT_SCHEMA
    reasoning_mode: LlmReasoningMode = LlmReasoningMode.OFF
    reasoning_effort: LlmReasoningEffort | None = None

    @model_validator(mode="after")
    def validate_reasoning(self) -> "LlmRoleSettings":
        if self.reasoning_mode is LlmReasoningMode.OFF and self.reasoning_effort is not None:
            raise ValueError("reasoning_effort_requires_reasoning_on")
        return self


class LlmRuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fast_research: LlmRoleSettings = Field(
        default_factory=lambda: LlmRoleSettings(
            profile_name="routerai-qwen35-9b",
            temperature=0.0,
            max_tokens=1200,
            timeout_seconds=15.0,
            reasoning_mode=LlmReasoningMode.OFF,
        )
    )
    extraction: LlmRoleSettings = Field(
        default_factory=lambda: LlmRoleSettings(
            profile_name="routerai-current",
            temperature=0.1,
            max_tokens=8192,
            timeout_seconds=120.0,
            reasoning_mode=LlmReasoningMode.OFF,
        )
    )
    reasoning: LlmRoleSettings = Field(
        default_factory=lambda: LlmRoleSettings(
            profile_name="routerai-current",
            temperature=0.1,
            max_tokens=8192,
            timeout_seconds=180.0,
            reasoning_mode=LlmReasoningMode.ON,
            reasoning_effort=LlmReasoningEffort.HIGH,
        )
    )

    def for_role(self, role: LlmRole | str) -> LlmRoleSettings:
        normalized = LlmRole(role)
        return getattr(self, normalized.value)


class LlmRuntimeSettingsRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settings: LlmRuntimeSettings = Field(default_factory=LlmRuntimeSettings)
    updated_at: str | None = None
    updated_by: int | None = None
    reason: str | None = None


class ResolvedLlmRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: LlmRole
    profile_name: str
    provider: str
    base_url: str
    api_key: str
    model: str
    configured: bool
    temperature: float
    max_tokens: int
    timeout_seconds: float
    output_mode: LlmOutputMode
    reasoning_mode: LlmReasoningMode
    reasoning_effort: LlmReasoningEffort | None

    def safe_descriptor(self) -> dict[str, object]:
        return {
            "role": self.role.value,
            "profile_name": self.profile_name,
            "provider": self.provider,
            "model": self.model,
            "configured": self.configured,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout_seconds,
            "output_mode": self.output_mode.value,
            "reasoning_mode": self.reasoning_mode.value,
            "reasoning_effort": (
                self.reasoning_effort.value if self.reasoning_effort is not None else None
            ),
        }


class LlmRuntimeSettingsRepository:
    def __init__(self, path: str | Path | None = None) -> None:
        configured = path or os.getenv("AIMETON_RUNTIME_DB", "data/runtime-core.sqlite3")
        self.path = Path(configured)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS runtime_meta "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )

    def get(self) -> LlmRuntimeSettingsRecord:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT value FROM runtime_meta WHERE key = ?", (SETTINGS_KEY,)
            ).fetchone()
        if row is None:
            return LlmRuntimeSettingsRecord()
        try:
            return LlmRuntimeSettingsRecord.model_validate_json(row["value"])
        except Exception:
            return LlmRuntimeSettingsRecord()

    def ensure_bootstrap_default(self) -> LlmRuntimeSettingsRecord:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT value FROM runtime_meta WHERE key = ?", (SETTINGS_KEY,)
            ).fetchone()
            if row is None:
                record = LlmRuntimeSettingsRecord(
                    settings=LlmRuntimeSettings(),
                    updated_at=datetime.now(UTC).isoformat(),
                    updated_by=None,
                    reason="system_bootstrap_default",
                )
                db.execute(
                    "INSERT OR IGNORE INTO runtime_meta(key, value) VALUES(?, ?)",
                    (SETTINGS_KEY, record.model_dump_json()),
                )
        return self.get()

    def save(
        self,
        settings: LlmRuntimeSettings,
        *,
        actor_id: int,
        reason: str,
    ) -> LlmRuntimeSettingsRecord:
        normalized_reason = " ".join(reason.split())
        if not normalized_reason:
            raise ValueError("reason_required")
        for role in LlmRole:
            config = settings.for_role(role)
            try:
                profile = observer_profile(config.profile_name)
            except KeyError as exc:
                raise ValueError(f"unknown_llm_profile:{config.profile_name}") from exc
            if profile.provider.value != "routerai":
                raise ValueError(f"runtime_llm_profile_must_use_routerai:{config.profile_name}")
        record = LlmRuntimeSettingsRecord(
            settings=settings,
            updated_at=datetime.now(UTC).isoformat(),
            updated_by=actor_id,
            reason=normalized_reason[:500],
        )
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO runtime_meta(key, value) VALUES(?, ?)",
                (SETTINGS_KEY, record.model_dump_json()),
            )
        return record


def get_llm_runtime_settings_repository() -> LlmRuntimeSettingsRepository:
    return LlmRuntimeSettingsRepository()


def resolve_llm_runtime(
    role: LlmRole | str,
    *,
    settings: LlmRuntimeSettings | None = None,
) -> ResolvedLlmRuntime:
    normalized_role = LlmRole(role)
    active = settings or get_llm_runtime_settings_repository().get().settings
    config = active.for_role(normalized_role)
    try:
        profile = observer_profile(config.profile_name)
        if profile.provider.value != "routerai":
            raise RuntimeError(f"runtime_llm_profile_must_use_routerai:{config.profile_name}")
        resolved_profile = profile.resolve()
    except KeyError as exc:
        raise RuntimeError(f"unknown_llm_profile:{config.profile_name}") from exc
    resolved_model = (\n        config.model_id\n        or resolved_profile.model\n        or ("openai/gpt-4o-mini" if config.profile_name == "routerai-current" else "")\n    ).strip()\n    return ResolvedLlmRuntime(
        role=normalized_role,
        profile_name=config.profile_name,
        provider=resolved_profile.provider.value,
        base_url=resolved_profile.base_url,
        api_key=resolved_profile.api_key,
        model=resolved_model,
        configured=bool(
            resolved_profile.base_url and resolved_profile.api_key and resolved_model
        ),
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        timeout_seconds=config.timeout_seconds,
        output_mode=config.output_mode,
        reasoning_mode=config.reasoning_mode,
        reasoning_effort=config.reasoning_effort,
    )
