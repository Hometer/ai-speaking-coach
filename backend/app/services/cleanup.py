"""录音清理：删除超过保留时限的上传录音。"""
import json
import os
import time
from datetime import datetime, timedelta, timezone

from app.core.config import settings


def cleanup_old_uploads(max_age_minutes: float | None = None) -> int:
    """删除 data/uploads 中超过保留时限的文件，返回删除数。"""
    max_age = max_age_minutes if max_age_minutes is not None else min(settings.upload_retention_minutes, settings.upload_max_age_minutes)
    uploads_dir = settings.data_dir / "uploads"
    if not uploads_dir.exists():
        return 0
    cutoff = time.time() - max_age * 60
    removed = 0
    for f in uploads_dir.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def cleanup_stale_uploads() -> int:
    """兑底清理：删除超过最大保留时限的录音（覆盖异常退出/失联录音）。"""
    return cleanup_old_uploads(max_age_minutes=settings.upload_max_age_minutes)


def cleanup_old_recordings() -> int:
    """清除已落库的本人录音及回放引用；教练合成音频仍随会话保留。"""
    from app.models.database import SessionLocal
    from app.models.entities import AsrRiskRecord, DrillAttempt, IntentResult, RequestOperation, Session, Turn

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=min(settings.upload_retention_minutes, settings.upload_max_age_minutes))
    removed = 0
    expired_urls = set()
    with SessionLocal() as db:
        turns = db.query(Turn).filter(Turn.recording_url != "").all()
        for turn in turns:
            created = turn.created_at
            if created is None or (created.replace(tzinfo=timezone.utc) if created.tzinfo is None else created) >= cutoff:
                continue
            filename = os.path.basename(turn.recording_url)
            path = settings.data_dir / "audio" / filename
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            expired_urls.add(turn.recording_url)
            turn.recording_url = ""
            if turn.practice_eval:
                try:
                    result = json.loads(turn.practice_eval)
                    result.pop("recording_url", None)
                    turn.practice_eval = json.dumps(result, ensure_ascii=False)
                except (TypeError, ValueError):
                    pass
            removed += 1
        # risk_hold 尚未形成 Turn；其音频有独立引用，不能漏过留存上限。
        for risk in db.query(AsrRiskRecord).filter(AsrRiskRecord.recording_url != "").all():
            created = risk.created_at
            if created is None or (created.replace(tzinfo=timezone.utc) if created.tzinfo is None else created) >= cutoff:
                continue
            filename = os.path.basename(risk.recording_url)
            path = settings.data_dir / "audio" / filename
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            session = db.get(Session, risk.session_id)
            if session is not None and session.pending_risk:
                try:
                    pending = json.loads(session.pending_risk)
                    if pending.get("recording_url") == risk.recording_url:
                        pending["recording_url"] = ""
                        session.pending_risk = json.dumps(pending, ensure_ascii=False)
                except (ValueError, TypeError, AttributeError):
                    pass
            expired_urls.add(risk.recording_url)
            risk.recording_url = ""
            removed += 1
        for attempt in db.query(DrillAttempt).filter(DrillAttempt.recording_url != "").all():
            created = attempt.created_at
            path = settings.data_dir / "audio" / os.path.basename(attempt.recording_url)
            if created and (created.replace(tzinfo=timezone.utc) if created.tzinfo is None else created) >= cutoff and path.is_file() and path.stat().st_mtime >= cutoff.timestamp():
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            expired_urls.add(attempt.recording_url)
            attempt.recording_url = ""
            removed += 1
        # 失效播放地址也从幂等快照撤销；业务判定和用户回答继续保留。
        if expired_urls:
            def strip(value):
                if isinstance(value, dict):
                    return {k: strip(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [strip(v) for v in value]
                return "" if isinstance(value, str) and value in expired_urls else value
            for model in (IntentResult, RequestOperation):
                for row in db.query(model).filter(model.response_json != "").all():
                    try:
                        row.response_json = json.dumps(strip(json.loads(row.response_json)), ensure_ascii=False)
                    except (ValueError, TypeError):
                        pass
        db.commit()
    return removed


def cleanup_expired_sessions() -> int:
    """按文本留存期限删除整条会话，并传播清理其派生证据。"""
    from app.models.database import SessionLocal
    from app.models.entities import Session
    from app.services.session_svc import delete_session

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=settings.session_retention_days)
    removed = 0
    with SessionLocal() as db:
        expired = db.query(Session).filter(Session.started_at < cutoff).all()
        for session in expired:
            delete_session(db, session.id, session.user_id)
            removed += 1
    return removed


def cleanup_idle_sessions() -> int:
    """空闲到时收尾：活跃时长超过练习时长上限的会话主动结束（不等待用户提交新回合）。"""
    from app.models.database import SessionLocal
    from app.models.entities import Session
    from app.services.session_svc import _active_elapsed_seconds, finalize_session

    ended = 0
    with SessionLocal() as db:
        active = db.query(Session).filter(
            Session.status.in_(["PREPARING", "WAITING_INPUT", "PRACTICE", "RECHECK"])
        ).all()
        for s in active:
            try:
                limit = (s.duration_minutes or 5.0) * 60
                if _active_elapsed_seconds(s) > limit:
                    s.status = "ENDED"
                    s.end_reason = "time_limit"
                    s.ended_at = datetime.now(timezone.utc)
                    finalize_session(db, s)  # 内部 commit
                    ended += 1
            except Exception:
                db.rollback()
                continue
    return ended


def cleanup_expired_audio() -> None:
    """服务启动及运行中调用，异常退出后在下次启动继续清理。"""
    cleanup_old_uploads()
    cleanup_old_recordings()


def cleanup_expired_data() -> None:
    cleanup_expired_audio()
    from app.services.qa_recording import cleanup_expired as cleanup_qa_recordings
    cleanup_qa_recordings()
    cleanup_idle_sessions()
    cleanup_expired_sessions()
    cleanup_audit_state()


def cleanup_audit_state():
    from app.models.database import SessionLocal
    from app.models.entities import RequestOperation, Memory, MemorySource, EventLog, ProductEvent, ReviewSurvey, ReviewSurveyAnswer, RecoveryAttempt
    cutoff=datetime.now(timezone.utc)-timedelta(days=30)
    with SessionLocal() as db:
        db.query(RequestOperation).filter(RequestOperation.created_at < cutoff).delete()
        db.query(EventLog).filter(EventLog.created_at < cutoff).delete()
        db.query(ProductEvent).filter(ProductEvent.created_at < cutoff).delete()
        db.query(RecoveryAttempt).filter(RecoveryAttempt.created_at < cutoff).delete()
        db.query(ReviewSurvey).filter(ReviewSurvey.shown_at < cutoff).delete()
        db.query(ReviewSurveyAnswer).filter(ReviewSurveyAnswer.answered_at < cutoff).delete()
        expired=[m.id for m in db.query(Memory).filter(Memory.expires_at.isnot(None),Memory.expires_at < datetime.now(timezone.utc)).all()]
        db.query(MemorySource).filter(MemorySource.memory_id.in_(expired)).delete()
        db.query(Memory).filter(Memory.id.in_(expired)).delete()
        db.commit()
