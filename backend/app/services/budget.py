"""成本护栏：调用前预留、超额即拒（确定性框架，真实单价待试算）。"""
from datetime import date

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.core.config import settings
from app.core.errors import AppError
from app.models.entities import BudgetLedger


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
    return f"{key}:{date.today().isoformat()}"


class BudgetGuard:
    def __init__(self, db: DbSession):
        self.db = db

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
            used = func.coalesce(BudgetLedger.spent, 0) + func.coalesce(BudgetLedger.reserved, 0)
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
        self.db.commit()

    def release(self, amount: float, session_id: str, user_id: str) -> None:
        """失败时释放预留额度，不计入已花费。"""
        for scope, key in [("session", session_id), ("user", _day_key(user_id)), ("project", _day_key("all"))]:
            row = self._row(scope, key)
            row.reserved = max(0.0, (row.reserved or 0.0) - amount)
        self.db.commit()

    def settle(self, amount: float, session_id: str, user_id: str) -> None:
        """按估算用量结算：把预留转为已花费（用于预算上限记账）。"""
        for scope, key in [
            ("session", session_id),
            ("user", _day_key(user_id)),
            ("project", _day_key("all")),
        ]:
            row = self._row(scope, key)
            row.reserved = max(0.0, (row.reserved or 0.0) - amount)
            row.spent = (row.spent or 0.0) + amount
        self.db.commit()
