"""七日训练计划请求/响应结构。"""
from pydantic import BaseModel, Field


class CreatePlanRequest(BaseModel):
    goal_type: str = "job_prep"
    available_minutes: int = Field(default=5, ge=5, le=15)


class PlanDay(BaseModel):
    day: int
    scene_id: str
    scene_label: str
    status: str
    retest_item_id: int | None = None
    adjustment_reason: str = ""
    scheduled_date: str = ""


class PlanResponse(BaseModel):
    id: int
    goal_type: str
    available_minutes: int = 5
    days: list[PlanDay] = []
    status: str = "active"
