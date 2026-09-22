"""七日训练计划请求/响应结构。"""
from pydantic import BaseModel


class CreatePlanRequest(BaseModel):
    goal_type: str = "job_prep"


class PlanDay(BaseModel):
    day: int
    scene_id: str
    scene_label: str
    status: str
    retest_item_id: int | None = None
    adjustment_reason: str = ""


class PlanResponse(BaseModel):
    id: int
    goal_type: str
    days: list[PlanDay] = []
    status: str = "active"
