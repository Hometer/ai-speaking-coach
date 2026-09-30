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
            "recording_url": "VARCHAR(255) DEFAULT ''",
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
            "daily_review_items": "TEXT DEFAULT '[]'",
            "daily_review_index": "INTEGER DEFAULT 0",
        }
        for col, ddl in sadditions.items():
            if col not in scols:
                conn.execute(text(f"ALTER TABLE sessions ADD COLUMN {col} {ddl}"))
        qcols = {r[1] for r in conn.execute(text("PRAGMA table_info(review_queue_items)"))}
        qadditions = {
            "interval_days": "INTEGER DEFAULT 1",
        }
        for col, ddl in qadditions.items():
            if col not in qcols:
                conn.execute(text(f"ALTER TABLE review_queue_items ADD COLUMN {col} {ddl}"))

        # budget_ledger：补实际用量列 + 唯一约束（先合并存量重复再建唯一索引）
        bcols = {r[1] for r in conn.execute(text("PRAGMA table_info(budget_ledger)"))}
        if "actual_spent" not in bcols:
            conn.execute(text("ALTER TABLE budget_ledger ADD COLUMN actual_spent FLOAT DEFAULT 0.0"))
        conn.execute(text(
            "UPDATE budget_ledger SET "
            "reserved = reserved + COALESCE((SELECT SUM(d.reserved) FROM budget_ledger d "
            "  WHERE d.scope = budget_ledger.scope AND d.scope_key = budget_ledger.scope_key "
            "  AND d.id != budget_ledger.id), 0), "
            "spent = spent + COALESCE((SELECT SUM(d.spent) FROM budget_ledger d "
            "  WHERE d.scope = budget_ledger.scope AND d.scope_key = budget_ledger.scope_key "
            "  AND d.id != budget_ledger.id), 0), "
            "actual_spent = actual_spent + COALESCE((SELECT SUM(d.actual_spent) FROM budget_ledger d "
            "  WHERE d.scope = budget_ledger.scope AND d.scope_key = budget_ledger.scope_key "
            "  AND d.id != budget_ledger.id), 0) "
            "WHERE id IN (SELECT MIN(id) FROM budget_ledger GROUP BY scope, scope_key)"
        ))
        conn.execute(text(
            "DELETE FROM budget_ledger WHERE id NOT IN ("
            "  SELECT MIN(id) FROM budget_ledger GROUP BY scope, scope_key"
            ")"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_budget_scope_key "
            "ON budget_ledger(scope, scope_key)"
        ))

        # memories：补来源回合/场景/版本/到期列
        mcols = {r[1] for r in conn.execute(text("PRAGMA table_info(memories)"))}
        madditions = {
            "source_turn_id": "VARCHAR(64) DEFAULT ''",
            "scene_id": "VARCHAR(32) DEFAULT ''",
            "version": "VARCHAR(16) DEFAULT 'v1'",
            "expires_at": "DATETIME",
        }
        for col, ddl in madditions.items():
            if col not in mcols:
                conn.execute(text(f"ALTER TABLE memories ADD COLUMN {col} {ddl}"))

        # 学习证据：队列中文意图/服务端帮助，尝试输入模态/帮助/幂等键
        rqcols = {r[1] for r in conn.execute(text("PRAGMA table_info(review_queue_items)"))}
        rqadditions = {
            "meaning_zh": "TEXT DEFAULT ''",
            "help_shown": "BOOLEAN DEFAULT 0",
            "last_attempt_key": "VARCHAR(64) DEFAULT ''",
            "last_attempt_answer": "TEXT DEFAULT ''",
        }
        for col, ddl in rqadditions.items():
            if col not in rqcols:
                conn.execute(text(f"ALTER TABLE review_queue_items ADD COLUMN {col} {ddl}"))
        dcols = {r[1] for r in conn.execute(text("PRAGMA table_info(drill_attempts)"))}
        dadditions = {
            "input_type": "VARCHAR(16) DEFAULT 'text'",
            "help_shown": "BOOLEAN DEFAULT 0",
            "attempt_key": "VARCHAR(64) DEFAULT ''",
        }
        for col, ddl in dadditions.items():
            if col not in dcols:
                conn.execute(text(f"ALTER TABLE drill_attempts ADD COLUMN {col} {ddl}"))
        dracols = {r[1] for r in conn.execute(text("PRAGMA table_info(daily_review_attempts)"))}
        draadditions = {
            "input_type": "VARCHAR(16) DEFAULT 'text'",
            "help_shown": "BOOLEAN DEFAULT 0",
            "attempt_key": "VARCHAR(64) DEFAULT ''",
        }
        for col, ddl in draadditions.items():
            if col not in dracols:
                conn.execute(text(f"ALTER TABLE daily_review_attempts ADD COLUMN {col} {ddl}"))

        # 幂等键 + 处理中标记
        scol2 = {r[1] for r in conn.execute(text("PRAGMA table_info(sessions)"))}
        if "request_key" not in scol2:
            conn.execute(text("ALTER TABLE sessions ADD COLUMN request_key VARCHAR(64)"))
        if "in_flight" not in scol2:
            conn.execute(text("ALTER TABLE sessions ADD COLUMN in_flight BOOLEAN DEFAULT 0"))
        if "level" not in scol2:
            conn.execute(text("ALTER TABLE sessions ADD COLUMN level VARCHAR(16) DEFAULT 'basic'"))
        if "pending_risk" not in scol2:
            conn.execute(text("ALTER TABLE sessions ADD COLUMN pending_risk TEXT DEFAULT ''"))
        tcol2 = {r[1] for r in conn.execute(text("PRAGMA table_info(turns)"))}
        if "request_key" not in tcol2:
            conn.execute(text("ALTER TABLE turns ADD COLUMN request_key VARCHAR(64)"))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_sessions_request_key "
            "ON sessions(request_key) WHERE request_key IS NOT NULL AND request_key != ''"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_turns_request_key "
            "ON turns(request_key) WHERE request_key IS NOT NULL AND request_key != ''"
        ))

        # 语块卡：并发同来源唯一
        conn.execute(text(
            "DELETE FROM phrase_cards WHERE id NOT IN ("
            "  SELECT MIN(id) FROM phrase_cards GROUP BY user_id, source_turn_id"
            ")"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_phrase_card_source "
            "ON phrase_cards(user_id, source_turn_id)"
        ))

        # 事件埋点：请求去重键
        ecols = {r[1] for r in conn.execute(text("PRAGMA table_info(event_logs)"))}
        if "request_key" not in ecols:
            conn.execute(text("ALTER TABLE event_logs ADD COLUMN request_key VARCHAR(64)"))
