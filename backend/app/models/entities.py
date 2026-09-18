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
    rechecked: Mapped[bool] = mapped_column(default=False)
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    session: Mapped["Session"] = relationship(back_populates="turns")


class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    turn_id: Mapped[str] = mapped_column(String(64), default="")
    result: Mapped[str] = mapped_column(String(32))
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
