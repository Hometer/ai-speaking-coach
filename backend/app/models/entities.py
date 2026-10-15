"""SQLAlchemy 实体模型（阶段 2.2 初版 schema）。"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Index, text
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
    level: Mapped[str] = mapped_column(String(16), default="basic")  # 创建时水平（再练保留）
    end_reason: Mapped[str] = mapped_column(String(32), nullable=True)
    review: Mapped[str] = mapped_column(Text, default="")
    subgoals: Mapped[str] = mapped_column(Text, default="{}")
    duration_minutes: Mapped[float] = mapped_column(Float, default=5.0)
    practice_type: Mapped[str] = mapped_column(String(24), default="interview")
    scene_id: Mapped[str] = mapped_column(String(32), default="job_interview")
    scene_version: Mapped[str] = mapped_column(String(16), default="v1")
    # 脱稿阶梯（P1-1）
    drill_stage: Mapped[str] = mapped_column(String(16), default="")
    drill_target: Mapped[str] = mapped_column(Text, default="")
    drill_meaning: Mapped[str] = mapped_column(Text, default="")
    drill_chunk: Mapped[str] = mapped_column(Text, default="")
    # 今日复练（P1-2）
    daily_review_items: Mapped[str] = mapped_column(Text, default="[]")
    daily_review_index: Mapped[int] = mapped_column(Integer, default=0)
    source_session_id: Mapped[str] = mapped_column(String(64), default="")
    source_turn_id: Mapped[str] = mapped_column(String(64), default="")
    practice_sentence: Mapped[str] = mapped_column(Text, default="")
    practice_goal: Mapped[str] = mapped_column(Text, default="")
    source_invalid: Mapped[bool] = mapped_column(default=False)
    rechecked: Mapped[bool] = mapped_column(default=False)
    active_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    active_since: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    # 当前有效问题（求助与评估按此绑定，不靠“最后一条回复”猜）
    current_question_id: Mapped[str] = mapped_column(String(64), default="")
    current_question_text: Mapped[str] = mapped_column(Text, default="")
    current_question_audio_url: Mapped[str] = mapped_column(String(255), default="")
    request_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # 创建会话幂等键
    in_flight: Mapped[bool] = mapped_column(default=False)  # 处理中标记（刷新恢复）
    initiation: Mapped[str] = mapped_column(String(16), default="self")
    state_version: Mapped[int] = mapped_column(Integer, default=0)
    paused_from: Mapped[str] = mapped_column(String(32), default="")
    selection_id: Mapped[str] = mapped_column(String(64), default="")
    device_check_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pending_risk: Mapped[str] = mapped_column(Text, default="")  # 待确认可疑转写（REQ-32，刷新恢复）
    plan_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    speech_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    # 本回合绑定的有效问题；帮助回合据此区分“理解辅助”与“答案辅助”
    question_id: Mapped[str] = mapped_column(String(64), default="")
    is_answer_help: Mapped[bool] = mapped_column(default=False)
    audio_status: Mapped[str] = mapped_column(String(16), default="ok")  # pending / ok / failed
    recording_url: Mapped[str] = mapped_column(String(255), default="")  # 本人录音回放（会话内）
    request_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # 提交回合幂等键
    request_hash: Mapped[str] = mapped_column(String(64), default="")
    result_status: Mapped[str] = mapped_column(String(32), default="")
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
    feedback_json: Mapped[str] = mapped_column(Text, default="[]")
    feedback_origin: Mapped[str] = mapped_column(String(24), default="unknown")
    revision_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class BudgetLedger(Base):
    __tablename__ = "budget_ledger"
    __table_args__ = (UniqueConstraint("scope", "scope_key", name="ux_budget_scope_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(16))  # session / user / project
    scope_key: Mapped[str] = mapped_column(String(128), index=True)
    reserved: Mapped[float] = mapped_column(Float, default=0.0)
    spent: Mapped[float] = mapped_column(Float, default=0.0)  # 估算记账（用于预算上限）
    actual_spent: Mapped[float] = mapped_column(Float, default=0.0)  # 旧版推算值；不可当供应商实账使用
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ProviderUsageEvent(Base):
    """供应商原始用量与调用尝试；金额需账单核对后另行确认。"""
    __tablename__ = "provider_usage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    turn_id: Mapped[str] = mapped_column(String(64), default="")
    component: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="")
    source: Mapped[str] = mapped_column(String(32), default="unreported")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    audio_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_bytes: Mapped[int] = mapped_column(Integer, default=0)
    text_chars: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class IntentResult(Base):
    """用户意图的首次响应快照，供后续状态改变后准确重放。"""
    __tablename__ = "intent_results"
    __table_args__ = (UniqueConstraint("scope", "object_id", "request_key", name="ux_intent_result"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    scope: Mapped[str] = mapped_column(String(32))
    object_id: Mapped[str] = mapped_column(String(64))
    request_key: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    source_session_id: Mapped[str] = mapped_column(String(64), default="")
    source_turn_id: Mapped[str] = mapped_column(String(64), default="")
    scene_id: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(16), default="candidate")
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[str] = mapped_column(String(16), default="v1")
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class MemorySource(Base):
    __tablename__ = "memory_sources"
    __table_args__ = (UniqueConstraint("memory_id", "session_id", "turn_id", name="ux_memory_source"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    memory_id: Mapped[int] = mapped_column(Integer, index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    turn_id: Mapped[str] = mapped_column(String(64), default="")
    revision_version: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class RequestOperation(Base):
    """外部调用前的持久认领；不保存请求正文或音频。"""
    __tablename__ = "request_operations"
    __table_args__ = (UniqueConstraint("user_id", "scope", "object_id", "request_key", name="ux_operation_intent"), Index("ux_operation_active_session", "session_id", unique=True, sqlite_where=text("status = 'running' AND session_id != '' AND parent_id = ''")))
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    scope: Mapped[str] = mapped_column(String(32))
    object_id: Mapped[str] = mapped_column(String(64))
    session_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    parent_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    request_key: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="running")
    response_json: Mapped[str] = mapped_column(Text, default="")
    error_code: Mapped[str] = mapped_column(String(64), default="")
    error_message: Mapped[str] = mapped_column(String(255), default="")
    error_status: Mapped[int] = mapped_column(Integer, default=409)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class BudgetReservation(Base):
    __tablename__ = "budget_reservations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    day: Mapped[str] = mapped_column(String(16))
    amount: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="reserved")
    provider_started: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class EventLog(Base):
    __tablename__ = "event_logs"
    __table_args__ = (Index("ux_event_request", "session_id", "request_key", unique=True, sqlite_where=text("request_key LIKE 'event:%'")),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    turn_id: Mapped[str] = mapped_column(String(64), default="")
    action_type: Mapped[str] = mapped_column(String(32), default="")
    basis: Mapped[str] = mapped_column(String(32), default="")
    input_type: Mapped[str] = mapped_column(String(16), default="")
    tool_result: Mapped[str] = mapped_column(String(32), default="")
    error: Mapped[str] = mapped_column(String(64), default="")
    request_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
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
    meaning_zh: Mapped[str] = mapped_column(Text, default="")
    attempt_stage: Mapped[str] = mapped_column(String(16), default="recall")
    question_version: Mapped[int] = mapped_column(Integer, default=1)
    question_text: Mapped[str] = mapped_column(Text, default="")
    help_shown: Mapped[bool] = mapped_column(default=False)  # 本次到期任务是否看过答案
    evidence_status: Mapped[str] = mapped_column(String(16), default="active")
    status: Mapped[str] = mapped_column(String(24), default="pending")
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    interval_days: Mapped[int] = mapped_column(Integer, default=1)  # 复练间隔档位 1/3/7/14
    last_attempt_key: Mapped[str] = mapped_column(String(64), default="")  # 幂等键
    last_attempt_answer: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    due_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    last_practiced_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    retest_result: Mapped[str] = mapped_column(String(16), default="")


class ReviewDueEvent(Base):
    """不可变到期窗口快照；复练后只填写结果，供进步指标追溯。"""
    __tablename__ = "review_due_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    queue_item_id: Mapped[int] = mapped_column(Integer, index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result: Mapped[str] = mapped_column(String(24), default="")
    valid: Mapped[bool] = mapped_column(default=True)


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
    available_minutes: Mapped[int] = mapped_column(Integer, default=5)
    days_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(16), default="active")
    paused_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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


class QualityLabelChange(Base):
    """质检标签变更审计（AC10-19）：每次改动留旧值/新值/审核人。"""
    __tablename__ = "quality_label_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label_id: Mapped[int] = mapped_column(Integer, index=True)
    reviewer_id: Mapped[str] = mapped_column(String(64), default="")
    old_conclusion: Mapped[str] = mapped_column(String(24), default="")
    new_conclusion: Mapped[str] = mapped_column(String(24), default="")
    old_review_version: Mapped[str] = mapped_column(String(16), default="")
    new_review_version: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class QaRecordingConsent(Base):
    """用户对单次会话的质检录音特别授权。"""
    __tablename__ = "qa_recording_consents"
    __table_args__ = (UniqueConstraint("session_id", name="ux_qa_consent_session"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    granted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class QaRecording(Base):
    """特别授权后的独立录音副本；普通录音清理不依赖此表。"""
    __tablename__ = "qa_recordings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    turn_id: Mapped[str] = mapped_column(String(64), index=True)
    filename: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class QaRecordingAccess(Base):
    """审核员录音访问、拒绝和审核完成记录，不存正文或音频。"""
    __tablename__ = "qa_recording_access"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recording_id: Mapped[int] = mapped_column(Integer, index=True)
    reviewer_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(16), default="view")
    outcome: Mapped[str] = mapped_column(String(16), default="allowed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class DrillAttempt(Base):
    """脱稿阶梯一次尝试（P1-1）：绑定来源建议与阶梯阶段，帮助证据决定完成结论。"""
    __tablename__ = "drill_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    source_session_id: Mapped[str] = mapped_column(String(64), default="")
    source_turn_id: Mapped[str] = mapped_column(String(64), default="")
    drill_session_id: Mapped[str] = mapped_column(String(64), index=True)
    stage: Mapped[str] = mapped_column(String(16), default="")  # full_text/fill_blanks/structure/free_speak/transfer
    visible_help: Mapped[str] = mapped_column(Text, default="")  # 实际展示的帮助内容（脱敏）
    answer_text: Mapped[str] = mapped_column(Text, default="")
    input_type: Mapped[str] = mapped_column(String(16), default="text")
    help_shown: Mapped[bool] = mapped_column(default=False)
    attempt_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    result: Mapped[str] = mapped_column(String(16), default="")  # reference/hinted/independent/transfer/pending/failed
    ended_reason: Mapped[str] = mapped_column(String(16), default="")  # completed/skipped/user_ended/error
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    ended_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)


class DailyReviewAttempt(Base):
    """今日复练（P1-2）每条尝试记录，用于汇总与事件。"""
    __tablename__ = "daily_review_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    queue_item_id: Mapped[int] = mapped_column(Integer, default=0)
    attempt_answer: Mapped[str] = mapped_column(Text, default="")
    input_type: Mapped[str] = mapped_column(String(16), default="text")
    help_shown: Mapped[bool] = mapped_column(default=False)
    attempt_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    result: Mapped[str] = mapped_column(String(16), default="")  # completed/skipped/deferred/disputed/failed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AsrRiskRecord(Base):
    """语音转写风险记录（REQ-32）：识别风险等级、原因、确认/修订动作。"""
    __tablename__ = "asr_risk_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    turn_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    risk_level: Mapped[str] = mapped_column(String(8), default="low")  # low/mid/high
    reason_codes: Mapped[str] = mapped_column(String(64), default="")
    original_text: Mapped[str] = mapped_column(Text, default="")
    recording_url: Mapped[str] = mapped_column(String(255), default="")
    request_key: Mapped[str] = mapped_column(String(64), default="")
    request_hash: Mapped[str] = mapped_column(String(64), default="")
    confirmed_text: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[str] = mapped_column(String(16), default="")  # none/confirm/rerecord/revise/text_fallback
    asr_rule_version: Mapped[str] = mapped_column(String(16), default="v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    resolved_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)


class ConversationQualityMark(Base):
    """对话质检标记（REQ-34）：直接提问应答、动作类型、连续同义追问、话题跳转。"""
    __tablename__ = "conversation_quality_marks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    turn_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    user_direct_question: Mapped[bool] = mapped_column(default=False)
    user_ask_explanation: Mapped[bool] = mapped_column(default=False)
    user_gave_info: Mapped[bool] = mapped_column(default=False)
    answered_direct_question: Mapped[bool | None] = mapped_column(nullable=True)
    review_status: Mapped[str] = mapped_column(String(16), default="unreviewed")
    coach_action: Mapped[str] = mapped_column(String(32), default="")
    repeated_synonym_followup: Mapped[bool] = mapped_column(default=False)
    topic_jump: Mapped[bool] = mapped_column(default=False)
    subgoal_id: Mapped[str] = mapped_column(String(16), default="")
    prompt_version: Mapped[str] = mapped_column(String(16), default="")
    scenario_version: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PhraseCard(Base):
    """语块卡（P1-4，REQ-31）：从有效建议生成，不扩写用户事实，随来源传播失效。"""
    __tablename__ = "phrase_cards"
    __table_args__ = (UniqueConstraint("user_id", "source_turn_id", name="ux_phrase_card_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    source_session_id: Mapped[str] = mapped_column(String(64), default="")
    source_turn_id: Mapped[str] = mapped_column(String(64), default="")
    original_text: Mapped[str] = mapped_column(Text, default="")
    suggestion_text: Mapped[str] = mapped_column(Text, default="")
    chunk_text: Mapped[str] = mapped_column(Text, default="")
    explanation_zh: Mapped[str] = mapped_column(Text, default="")
    context_zh: Mapped[str] = mapped_column(Text, default="")
    replaceable: Mapped[str] = mapped_column(Text, default="")
    example_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / invalid
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class RecallEvidence(Base):
    __tablename__ = "recall_evidence"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    queue_item_id: Mapped[int] = mapped_column(Integer, index=True)
    question_version: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(16))
    question: Mapped[str] = mapped_column(Text, default="")
    input_type: Mapped[str] = mapped_column(String(16))
    help_shown: Mapped[bool] = mapped_column(default=False)
    result: Mapped[str] = mapped_column(String(32))
    request_key: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ConversationQualityReview(Base):
    __tablename__ = "conversation_quality_reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    turn_id: Mapped[str] = mapped_column(String(64), index=True)
    reviewer_id: Mapped[str] = mapped_column(String(64))
    conclusion_json: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(String(16))
    scenario_version: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ProductEvent(Base):
    """白名单产品动作，不保存正文或任意 JSON。"""
    __tablename__ = "product_events"
    __table_args__ = (UniqueConstraint("user_id", "action", "request_key", name="ux_product_event_key"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(24))
    request_key: Mapped[str] = mapped_column(String(64))
    session_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    scene_id: Mapped[str] = mapped_column(String(32), default="")
    stage: Mapped[str] = mapped_column(String(16), default="")
    turn_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ReviewSurvey(Base):
    __tablename__ = "review_surveys"
    __table_args__ = (UniqueConstraint("session_id", name="ux_review_survey_session"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    answer: Mapped[str] = mapped_column(String(16), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    shown_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RecoveryAttempt(Base):
    __tablename__ = "recovery_attempts"
    __table_args__ = (UniqueConstraint("operation_id", name="ux_recovery_operation"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    operation_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ReviewSurveyAnswer(Base):
    """仅存问卷答案版本和时间，不复制练习正文；随会话清理。"""
    __tablename__ = 'review_survey_answers'
    __table_args__ = (UniqueConstraint('session_id', 'version', name='ux_survey_answer_version'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    answer: Mapped[str] = mapped_column(String(16))
    answered_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class SupplierBillLine(Base):
    __tablename__ = "supplier_bill_lines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usage_event_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    reference: Mapped[str] = mapped_column(String(128), unique=True)
    amount: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
