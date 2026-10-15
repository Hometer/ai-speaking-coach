from typing import Literal
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


class ProductEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["scene_select", "stage_view", "stage_start"]
    request_key: str = Field(min_length=1, max_length=64)
    session_id: str = Field(default="", max_length=64)
    scene_id: str = Field(default="", max_length=32)
    stage: Literal["", "free_speak", "transfer"] = ""


class SurveyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: Literal["shown", "helpful", "not_helpful", "uncertain"]


class RecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=1, max_length=64)


class MetricValue(BaseModel):
    label: str
    numerator: int
    denominator: int
    rate: float | None
    unknown: int = 0
    basis: str


class ProductMetrics(BaseModel):
    version: str
    observation_days: int
    eligible_users: int
    active_review_users: int
    active_review_rate: float | None
    full_scene_review_users: int
    full_scene_review_rate: float | None
    first_practice_users: int
    completed_scene_count: int
    started_scene_count: int
    pending_observation_users: int
    speech_measurement: str
    window: dict[str, str]
    metrics: dict[str, MetricValue]
    counts: dict[str, int]
