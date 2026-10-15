"""质检录音特别授权、独立副本、访问审计与七天清理。"""
from app.core.dates import utc_iso
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session as DbSession

from app.core.config import settings
from app.core.errors import AppError
from app.models.entities import QaRecording, QaRecordingAccess, QaRecordingConsent, Session


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def _path(filename: str) -> Path:
    safe = os.path.basename(filename)
    if safe != filename or not safe:
        raise AppError("BAD_FILENAME", "非法的录音文件名", status_code=400)
    return settings.data_dir / "qa_audio" / safe


def _active(consent: QaRecordingConsent | None) -> bool:
    return bool(consent and consent.granted_at and not consent.revoked_at and consent.expires_at and _aware(consent.expires_at) > _now())


def consent_status(db: DbSession, user_id: str, session_id: str) -> dict[str, object]:
    session = db.get(Session, session_id)
    if session is None or session.user_id != user_id:
        raise AppError("FORBIDDEN", "无权访问该会话", status_code=403)
    row = db.query(QaRecordingConsent).filter_by(session_id=session_id, user_id=user_id).first()
    return {"granted": _active(row), "expires_at": utc_iso(row.expires_at) if _active(row) else None}


def set_consent(db: DbSession, user_id: str, session_id: str, granted: bool) -> dict[str, object]:
    session = db.get(Session, session_id)
    if session is None or session.user_id != user_id:
        raise AppError("FORBIDDEN", "无权访问该会话", status_code=403)
    row = db.query(QaRecordingConsent).filter_by(session_id=session_id, user_id=user_id).first()
    if row is None:
        row = QaRecordingConsent(user_id=user_id, session_id=session_id)
        db.add(row)
    if granted:
        if session.status == "ENDED":
            raise AppError("INVALID_STATE", "练习已结束，不能补授权录音", status_code=409)
        row.granted_at = _now()
        row.revoked_at = None
        row.expires_at = _now() + timedelta(days=7)
    else:
        row.revoked_at = _now()
        for recording in db.query(QaRecording).filter_by(session_id=session_id).all():
            if not recording.deleted_at:
                _path(recording.filename).unlink(missing_ok=True)
                recording.deleted_at = _now()
    db.commit()
    return consent_status(db, user_id, session_id)


def capture_if_consented(db: DbSession, user_id: str, session_id: str, turn_id: str, audio_bytes: bytes, ext: str) -> None:
    """普通语音回合已经持久化后调用；未授权时绝不创建质检副本。"""
    consent = db.query(QaRecordingConsent).filter_by(session_id=session_id, user_id=user_id).first()
    if not _active(consent):
        return
    filename = f"{uuid.uuid4().hex}.{ext}"
    path = _path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(audio_bytes)
    try:
        db.add(QaRecording(user_id=user_id, session_id=session_id, turn_id=turn_id, filename=filename, expires_at=min(_aware(consent.expires_at), _now() + timedelta(days=7))))
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise


def playback_path(db: DbSession, reviewer_id: str, recording_id: int) -> Path:
    recording = db.get(QaRecording, recording_id)
    consent = db.query(QaRecordingConsent).filter_by(session_id=recording.session_id).first() if recording else None
    allowed = bool(recording and not recording.deleted_at and _active(consent) and _aware(recording.expires_at) > _now())
    path = _path(recording.filename) if recording else None
    allowed = allowed and path.is_file()
    db.add(QaRecordingAccess(recording_id=recording_id, reviewer_id=reviewer_id, action="view", outcome="allowed" if allowed else "denied"))
    db.commit()
    if not allowed:
        raise AppError("FORBIDDEN", "录音未授权或已过期", status_code=403)
    return path


def complete_review(db: DbSession, reviewer_id: str, recording_id: int) -> dict[str, bool]:
    recording = db.get(QaRecording, recording_id)
    if recording is None:
        raise AppError("NOT_FOUND", "质检录音不存在", status_code=404)
    if not recording.deleted_at:
        _path(recording.filename).unlink(missing_ok=True)
        recording.deleted_at = _now()
    db.add(QaRecordingAccess(recording_id=recording_id, reviewer_id=reviewer_id, action="complete", outcome="allowed"))
    db.commit()
    return {"deleted": True}


def access_log(db: DbSession, recording_id: int) -> list[dict[str, str]]:
    return [
        {"reviewer_id": row.reviewer_id, "action": row.action, "outcome": row.outcome, "created_at": utc_iso(row.created_at) if row.created_at else ""}
        for row in db.query(QaRecordingAccess).filter_by(recording_id=recording_id).order_by(QaRecordingAccess.id).all()
    ]


def cleanup_expired() -> int:
    from app.models.database import SessionLocal

    removed = 0
    with SessionLocal() as db:
        for recording in db.query(QaRecording).filter(QaRecording.deleted_at.is_(None)).all():
            consent = db.query(QaRecordingConsent).filter_by(session_id=recording.session_id).first()
            if _active(consent) and _aware(recording.expires_at) > _now():
                continue
            _path(recording.filename).unlink(missing_ok=True)
            recording.deleted_at = _now()
            removed += 1
        db.commit()
    return removed


def remove_session(db: DbSession, session_id: str) -> None:
    for recording in db.query(QaRecording).filter_by(session_id=session_id).all():
        if not recording.deleted_at:
            _path(recording.filename).unlink(missing_ok=True)
        db.delete(recording)
    db.query(QaRecordingConsent).filter_by(session_id=session_id).delete()
