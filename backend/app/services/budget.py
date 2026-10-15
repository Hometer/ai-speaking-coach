"""成本护栏：调用前预留、超额即拒（确定性框架，真实单价待试算）。"""
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.core.config import settings
from app.core.errors import AppError
from app.models.entities import BudgetLedger, BudgetReservation
from app.services.operations import current_id


class ToolBudget:
    """单回合 Agent 工具调用次数上限（Harness 约束）。"""

    def __init__(self, limit: int | None = None):
        self.limit = limit if limit is not None else settings.max_tools_per_turn
        self.used = 0

    def consume(self) -> None:
        self.used += 1
        if self.used > self.limit:
            raise AppError("TOOL_LIMIT_EXCEEDED", "本回合工具调用次数已达上限", status_code=429)


def _day_key(key: str) -> str:
    """按自然日隔离，用户日/项目日预算每天自动重置。"""
    return f"{key}:{datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()}"


class BudgetGuard:
    def __init__(self, db: DbSession):
        self.db = db
        self.reservation_ids = []

    def _row(self, scope: str, key: str) -> BudgetLedger:
        row = self.db.query(BudgetLedger).filter_by(scope=scope, scope_key=key).first()
        if row is not None:
            return row
        row = BudgetLedger(scope=scope, scope_key=key, reserved=0.0, spent=0.0, actual_spent=0.0)
        self.db.add(row)
        try:
            self.db.flush()  # 立即写入，避免同一事务内重复建行；唯一约束兑底并发
        except IntegrityError:
            self.db.rollback()
            row = self.db.query(BudgetLedger).filter_by(scope=scope, scope_key=key).first()
            if row is None:
                raise
        return row

    @staticmethod
    def _used(row: BudgetLedger) -> float:
        return (row.spent or 0.0) + (row.reserved or 0.0)

    def reserve(self, amount: float, session_id: str, user_id: str) -> None:
        """原子预留：条件更新 + rowcount 校验，避免并发共同突破预算。"""
        caps = [
            ("session", session_id, settings.budget_session),
            ("user", _day_key(user_id), settings.budget_user_day),
            ("project", _day_key("all"), settings.budget_project_day),
        ]
        for scope, key, cap in caps:
            self._row(scope, key)  # 确保行存在
            used = func.max(func.coalesce(BudgetLedger.spent, 0), func.coalesce(BudgetLedger.actual_spent, 0)) + func.coalesce(BudgetLedger.reserved, 0)
            result = self.db.execute(
                update(BudgetLedger)
                .where(
                    BudgetLedger.scope == scope,
                    BudgetLedger.scope_key == key,
                    used + amount <= cap,
                )
                .values(reserved=func.coalesce(BudgetLedger.reserved, 0) + amount)
            )
            if result.rowcount == 0:
                self.db.rollback()
                raise AppError(
                    "BUDGET_EXCEEDED",
                    "费用预算已用尽，本次练习结束",
                    status_code=402,
                )
        reservation = BudgetReservation(id=uuid.uuid4().hex, operation_id=current_id(), session_id=session_id,
            user_id=user_id, day=_day_key("all").rsplit(":", 1)[1], amount=amount)
        self.db.add(reservation)
        self.db.commit()
        self.reservation_ids.append(reservation.id)

    def _finish(self, amount: float, session_id: str, user_id: str, status: str) -> None:
        q = self.db.query(BudgetReservation).filter_by(session_id=session_id, user_id=user_id, status="reserved")
        if self.reservation_ids:
            q = q.filter(BudgetReservation.id.in_(self.reservation_ids))
        elif current_id():
            q = q.filter_by(operation_id=current_id())
        row = q.order_by(BudgetReservation.created_at, BudgetReservation.id).first()
        if row is None:
            return  # 重复释放/结算不能影响另一个意图的预留。
        rid, day, reserved = row.id, row.day, row.amount
        changed = self.db.execute(update(BudgetReservation).where(BudgetReservation.id == rid,
            BudgetReservation.status == "reserved").values(status=status))
        if changed.rowcount != 1:
            self.db.rollback()
            return
        for scope, key in [("session", session_id), ("user", f"{user_id}:{day}"), ("project", f"all:{day}")]:
            values = {"reserved": func.max(0.0, func.coalesce(BudgetLedger.reserved, 0) - reserved)}
            if status == "settled":
                values["spent"] = func.coalesce(BudgetLedger.spent, 0) + reserved
            self.db.execute(update(BudgetLedger).where(BudgetLedger.scope == scope, BudgetLedger.scope_key == key).values(**values))
        self.db.commit()

    def release(self, amount: float, session_id: str, user_id: str) -> None:
        self._finish(amount, session_id, user_id, "released")

    def settle(self, amount: float, session_id: str, user_id: str) -> None:
        """预留估算结算；不将该金额冒称供应商实账。"""
        self._finish(amount, session_id, user_id, "settled")
