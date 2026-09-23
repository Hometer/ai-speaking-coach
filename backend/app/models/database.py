"""数据库引擎与会话。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app.models import entities  # noqa: F401  确保模型注册

    Base.metadata.create_all(bind=engine)
    migrate_db()


def migrate_db() -> None:
    """开发阶段轻量迁移：为已有 SQLite 表补列（幂等）。schema 稳定后换 Alembic。"""
    if not settings.database_url.startswith("sqlite"):
        return
    from sqlalchemy import text

    with engine.begin() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(sessions)"))}
        additions = {
            "scene_id": "VARCHAR(32) DEFAULT 'job_interview'",
            "scene_version": "VARCHAR(16) DEFAULT 'v1'",
            "active_seconds": "FLOAT DEFAULT 0.0",
            "active_since": "DATETIME",
            "current_question_id": "VARCHAR(64) DEFAULT ''",
            "current_question_text": "TEXT DEFAULT ''",
            "current_question_audio_url": "VARCHAR(255) DEFAULT ''",
        }
        for col, ddl in additions.items():
            if col not in cols:
                conn.execute(text(f"ALTER TABLE sessions ADD COLUMN {col} {ddl}"))
        tcols = {r[1] for r in conn.execute(text("PRAGMA table_info(turns)"))}
        tadditions = {
            "question_id": "VARCHAR(64) DEFAULT ''",
            "is_answer_help": "BOOLEAN DEFAULT 0",
            "audio_status": "VARCHAR(16) DEFAULT 'ok'",
        }
        for col, ddl in tadditions.items():
            if col not in tcols:
                conn.execute(text(f"ALTER TABLE turns ADD COLUMN {col} {ddl}"))
        scols = {r[1] for r in conn.execute(text("PRAGMA table_info(sessions)"))}
        sadditions = {
            "drill_stage": "VARCHAR(16) DEFAULT ''",
            "drill_target": "TEXT DEFAULT ''",
            "drill_meaning": "TEXT DEFAULT ''",
            "drill_chunk": "TEXT DEFAULT ''",
        }
        for col, ddl in sadditions.items():
            if col not in scols:
                conn.execute(text(f"ALTER TABLE sessions ADD COLUMN {col} {ddl}"))
