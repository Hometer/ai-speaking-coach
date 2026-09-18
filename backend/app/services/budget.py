"""成本护栏：调用前预留、超额即拒（确定性框架，真实单价待试算）。"""
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


class BudgetGuard:
    def __init__(self, db: DbSession):
        self.db = db

    def _row(self, scope: str, key: str) -> BudgetLedger:
        row = self.db.query(BudgetLedger).filter_by(scope=scope, scope_key=key).first()
        if row is None:
            row = BudgetLedger(scope=scope, scope_key=key, reserved=0.0, spent=0.0)
            self.db.add(row)
        return row

    @staticmethod
    def _used(row: BudgetLedger) -> float:
        return (row.spent or 0.0) + (row.reserved or 0.0)

    def reserve(self, amount: float, session_id: str, user_id: str) -> None:
        """预留费用；预计超额即拒绝，不等账单产生。"""
        caps = [
            ("session", session_id, settings.budget_session),
            ("user", user_id, settings.budget_user_day),
            ("project", "all", settings.budget_project_day),
        ]
        for scope, key, cap in caps:
            row = self._row(scope, key)
            if self._used(row) + amount > cap:
                raise AppError(
                    "BUDGET_EXCEEDED",
                    "费用预算已用尽，本次练习结束",
                    status_code=402,
                )
        for scope, key, _ in caps:
            row = self._row(scope, key)
            row.reserved = (row.reserved or 0.0) + amount
        self.db.commit()

    def settle(self, amount: float, session_id: str, user_id: str) -> None:
        """按实际用量结算：把预留转为已花费。"""
        for scope, key in [("session", session_id), ("user", user_id), ("project", "all")]:
            row = self._row(scope, key)
            row.reserved = max(0.0, (row.reserved or 0.0) - amount)
            row.spent = (row.spent or 0.0) + amount
        self.db.commit()
