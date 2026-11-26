"""Explicit v2 contracts; ingestion accepts bad rows for durable quarantine."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class LiveModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class CreateLive(LiveModel):
    timeline_id: str = Field(min_length=1, max_length=120)
    title: str = Field(default='增量轨迹会话', max_length=120)
    configuration: dict = Field(default_factory=dict)


class IngestLive(LiveModel):
    idempotency_key: str = Field(min_length=1, max_length=120)
    source_id: str = Field(min_length=1, max_length=120)
    timeline_id: str = Field(min_length=1, max_length=120)
    observations: list = Field(min_length=1)


class PrepareLive(LiveModel):
    purpose: Literal['association', 'causal_forecast', 'retrospective_reconstruction'] = 'association'
    observation_ids: list[str] | None = None
    knowledge_cutoff_seq: int | None = Field(default=None, ge=0)
    forecast_origin_time: float | None = Field(default=None, allow_inf_nan=False)
    horizon: float = Field(default=20, gt=0, le=600, allow_inf_nan=False)


class AuthorizeLive(LiveModel):
    idempotency_key: str = Field(min_length=1, max_length=120)
    snapshot_id: str
    expected_state_revision: int = Field(ge=0)
    context_digest: str = Field(pattern='^[a-f0-9]{64}$')
    consent: bool = False
    rerun_of: str | None = None
    duplicate_fee_ack: bool = False


class ReviewLive(LiveModel):
    expected_state_revision: int = Field(ge=0)
    decision: Literal['adopt', 'reject']
    reason: str = Field(default='', max_length=2000)


class ConfigureLive(LiveModel):
    expected_state_revision: int = Field(ge=0)
    configuration: dict


class MigrateLive(CreateLive):
    dataset: dict
    legacy_history: dict = Field(default_factory=dict)


class RetractLive(LiveModel):
    expected_state_revision: int = Field(ge=0)
    stable_ids: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=2000)

