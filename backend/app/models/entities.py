"""SQLAlchemy 实体模型（阶段 2.2 初版 schema）。"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    level: Mapped[str] = mapped_column(String(16), default="basic")
    memory_enabled: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="PREPARING")
    goal_text: Mapped[str] = mapped_column(Text, default="")
    difficulty: Mapped[str] = mapped_column(String(16), default="standard")
    end_reason: Mapped[str] = mapped_column(String(32), nullable=True)
    review: Mapped[str] = mapped_column(Text, default="")
    subgoals: Mapped[str] = mapped_column(Text, default="{}")
    duration_minutes: Mapped[float] = mapped_column(Float, default=5.0)
    practice_type: Mapped[str] = mapped_column(String(24), default="interview")
    scene_id: Mapped[str] = mapped_column(String(32), default="job_interview")
    scene_version: Mapped[str] = mapped_column(String(16), default="v1")
    source_session_id: Mapped[str] = mapped_column(String(64), default="")
    source_turn_id: Mapped[str] = mapped_column(String(64), default="")
    practice_sentence: Mapped[str] = mapped_column(Text, default="")
    practice_goal: Mapped[str] = mapped_column(Text, default="")
    source_invalid: Mapped[bool] = mapped_column(default=False)
    rechecked: Mapped[bool] = mapped_column(default=False)
    active_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    active_since: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    ended_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    turns: Mapped[list["Turn"]] = relationship(
        back_populates="session", order_by="Turn.seq", cascade="all, delete-orphan"
    )


class Turn(Base):
    __tablename__ = "turns"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("sessions.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    input_type: Mapped[str] = mapped_column(String(16), default="text")  # voice/text/practice
    transcript: Mapped[str] = mapped_column(Text, default="")
    user_text: Mapped[str] = mapped_column(Text, default="")
    reply_text: Mapped[str] = mapped_column(Text, default="")
    reply_audio_url: Mapped[str] = mapped_column(String(255), default="")
    action_type: Mapped[str] = mapped_column(String(32), default="")
    hint_used: Mapped[bool] = mapped_column(default=False)
    practice_sentence: Mapped[str] = mapped_column(Text, default="")
    practice_eval: Mapped[str] = mapped_column(Text, default="")
    eval_result: Mapped[str] = mapped_column(String(32), default="")
    revised_text: Mapped[str] = mapped_column(Text, default="")
    revision_version: Mapped[int] = mapped_column(Integer, default=0)
    help_type: Mapped[str] = mapped_column(String(16), default="")
    help_level: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    session: Mapped["Session"] = relationship(back_populates="turns")


class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    turn_id: Mapped[str] = mapped_column(String(64), default="")
    result: Mapped[str] = mapped_column(String(32))
    subgoal: Mapped[str] = mapped_column(String(16), default="")
    input_type: Mapped[str] = mapped_column(String(16), default="text")
    question: Mapped[str] = mapped_column(Text, default="")
    evidence_turn_ids: Mapped[str] = mapped_column(Text, default="")
    standard_version: Mapped[str] = mapped_column(String(16), default="v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class BudgetLedger(Base):
    __tablename__ = "budget_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(16))  # session / user / project
    scope_key: Mapped[str] = mapped_column(String(128), index=True)
    reserved: Mapped[float] = mapped_column(Float, default=0.0)
    spent: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    source_session_id: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="candidate")
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class EventLog(Base):
    __tablename__ = "event_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    turn_id: Mapped[str] = mapped_column(String(64), default="")
    action_type: Mapped[str] = mapped_column(String(32), default="")
    basis: Mapped[str] = mapped_column(String(32), default="")
    input_type: Mapped[str] = mapped_column(String(16), default="")
    tool_result: Mapped[str] = mapped_column(String(32), default="")
    error: Mapped[str] = mapped_column(String(64), default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    usage: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Invite(Base):
    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    revoked: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class DeviceCheck(Base):
    __tablename__ = "device_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    summary: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(16), default="ok")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ReviewQueueItem(Base):
    __tablename__ = "review_queue_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    source_session_id: Mapped[str] = mapped_column(String(64), default="")
    source_turn_id: Mapped[str] = mapped_column(String(64), default="")
    target_ability: Mapped[str] = mapped_column(String(32), default="")
    suggestion_sentence: Mapped[str] = mapped_column(Text, default="")
    evidence_status: Mapped[str] = mapped_column(String(16), default="active")
    status: Mapped[str] = mapped_column(String(24), default="pending")
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    due_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    last_practiced_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    retest_result: Mapped[str] = mapped_column(String(16), default="")


class ProgressSnapshot(Base):
    __tablename__ = "progress_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    window: Mapped[str] = mapped_column(String(16), default="7d")
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    calc_version: Mapped[str] = mapped_column(String(16), default="v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TrainingPlan(Base):
    __tablename__ = "training_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    goal_type: Mapped[str] = mapped_column(String(24), default="job_prep")
    days_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class QualityLabel(Base):
    __tablename__ = "quality_labels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    turn_id: Mapped[str] = mapped_column(String(64), default="")
    issue_type: Mapped[str] = mapped_column(String(24), default="")
    conclusion: Mapped[str] = mapped_column(String(24), default="")
    reviewer_id: Mapped[str] = mapped_column(String(64), default="")
    review_version: Mapped[str] = mapped_column(String(16), default="v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
