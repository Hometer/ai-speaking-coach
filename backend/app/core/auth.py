"""身份推导：服务端验证邀请码后推导 user_id，不信任客户端填写的身份。"""
from fastapi import Header

from app.core.errors import AppError
from app.models.database import SessionLocal
from app.models.entities import Invite

# 内测演示邀请码 → 独立测试身份（上线前由产品经理分发真实邀请码）
DEFAULT_INVITES = [
    {"code": "test-a", "user_id": "user-a"},
    {"code": "test-b", "user_id": "user-b"},
    {"code": "qa-a", "user_id": "qa-reviewer"},
]


def seed_invites() -> None:
    from app.core.config import settings
    from app.models.database import SessionLocal as S

    if not settings.seed_demo_invites:
        return
    db = S()
    try:
        for item in DEFAULT_INVITES:
            if db.query(Invite).filter(Invite.code == item["code"]).first() is None:
                db.add(Invite(code=item["code"], user_id=item["user_id"]))
        db.commit()
    finally:
        db.close()


def get_user_id(x_invite_code: str = Header(default="", alias="X-Invite-Code")) -> str:
    """FastAPI 依赖：从邀请码推导可信 user_id。"""
    code = (x_invite_code or "").strip()
    if not code:
        raise AppError("UNAUTHORIZED", "缺少邀请凭证", status_code=401)
    db = SessionLocal()
    try:
        invite = db.query(Invite).filter(Invite.code == code).first()
        if invite is None or invite.revoked:
            raise AppError("UNAUTHORIZED", "邀请凭证无效或已撤销", status_code=401)
        return invite.user_id
    finally:
        db.close()


def get_qa_user_id(x_invite_code: str = Header(default="", alias="X-Invite-Code")) -> str:
    """FastAPI 依赖：质检员身份（邀请码必须在 QA_INVITE_CODES 配置中）。"""
    from app.core.config import settings

    code = (x_invite_code or "").strip()
    if not code:
        raise AppError("UNAUTHORIZED", "缺少邀请凭证", status_code=401)
    if code not in settings.qa_invite_codes:
        raise AppError("FORBIDDEN", "无质检权限", status_code=403)
    db = SessionLocal()
    try:
        invite = db.query(Invite).filter(Invite.code == code).first()
        if invite is None or invite.revoked:
            raise AppError("UNAUTHORIZED", "邀请凭证无效或已撤销", status_code=401)
        return invite.user_id
    finally:
        db.close()
