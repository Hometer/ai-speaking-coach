"""录音清理：删除超过保留时限的上传录音。"""
import time

from app.core.config import settings


def cleanup_old_uploads(max_age_minutes: float | None = None) -> int:
    """删除 data/uploads 中超过保留时限的文件，返回删除数。"""
    max_age = max_age_minutes if max_age_minutes is not None else settings.upload_retention_minutes
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
