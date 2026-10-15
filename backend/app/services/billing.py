"""版本化费率估算和人工账单核对。没有费率/计量证据时金额保持未知。"""
import json
import os
from datetime import timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from zoneinfo import ZoneInfo
from sqlalchemy import update,func
from app.core.errors import AppError
from app.models.entities import SupplierBillLine, ProviderUsageEvent, BudgetLedger


def load_price_book():
    path=os.getenv('PRICE_BOOK_PATH','')
    if not path:
        return None
    try:
        data=json.loads(Path(path).read_text())
        if data.get('currency')!='CNY' or not isinstance(data.get('rates'),dict) or not data.get('version'):
            return None
        return data
    except (OSError,ValueError,TypeError):
        return None


def estimate(usage,book):
    if not book or usage.source=='unreported':
        return None
    rule=book['rates'].get(f'{usage.component}:{usage.model}')
    if not isinstance(rule,dict) or not rule.get('source'):
        return None
    try:
        def rate(key):
            value=Decimal(str(rule[key]))
            if not value.is_finite() or value<0:
                raise ValueError()
            return value
        if usage.component=='llm':
            cost=(usage.input_tokens*rate('input_per_million')+usage.output_tokens*rate('output_per_million'))/1000000
        elif usage.component=='tts':
            cost=usage.text_chars*usage.attempts*rate('per_character')
        elif 'per_call' in rule:
            cost=usage.attempts*rate('per_call')
        elif usage.audio_seconds is not None:
            unit=Decimal(str(rule.get('round_seconds',1)))
            if unit<=0:
                return None
            seconds=max(Decimal(str(usage.audio_seconds)),Decimal(str(rule.get('minimum_seconds',0))))
            rounded=(seconds/unit).to_integral_value(rounding=ROUND_CEILING)*unit
            cost=rounded*rate('per_second')*usage.attempts
        else:
            return None
        return float(cost.quantize(Decimal('0.000001')))
    except (KeyError,ValueError,TypeError,InvalidOperation):
        return None


def session_costs(db,session_id):
    usages=db.query(ProviderUsageEvent).filter_by(session_id=session_id).all()
    book=load_price_book()
    values=[estimate(u,book) for u in usages]
    bills=db.query(SupplierBillLine).filter(SupplierBillLine.usage_event_id.in_([u.id for u in usages])).all()
    billed_ids={b.usage_event_id for b in bills}
    return {'supplier_estimate':round(sum(values),6) if values and all(v is not None for v in values) else None,
        'billed_cost':round(sum(b.amount for b in bills),6) if usages and all(u.id in billed_ids for u in usages) else None,
        'unpriced_attempts':sum(v is None for v in values),'unreconciled_attempts':len(usages)-len(billed_ids),
        'rate_version':book['version'] if book else None}


def reconcile(db,event_id,amount,reference):
    """仅由运维账单导入脚本调用；唯一账单行、重复导入不重复计费。"""
    try:
        value=Decimal(str(amount))
        if not value.is_finite() or value<0 or not reference or len(reference)>128:
            raise ValueError()
    except (ValueError,TypeError,InvalidOperation):
        raise AppError('INVALID_BILL','账单行参数不合法',422)
    old=db.query(SupplierBillLine).filter_by(reference=reference).first()
    if old:
        if old.usage_event_id!=event_id or Decimal(str(old.amount))!=value:
            raise AppError('IDEMPOTENCY_CONFLICT','账单来源与既有行不一致',409)
        return
    usage=db.get(ProviderUsageEvent,event_id)
    if usage is None:
        raise AppError('NOT_FOUND','供应商调用记录不存在',404)
    if db.query(SupplierBillLine).filter_by(usage_event_id=event_id).first():
        raise AppError('ALREADY_RECONCILED','这次调用已经核对过账单',409)
    # 先 flush 唯一账单行，再在同一事务更新原始计费日，任一失败整体回滚。
    db.add(SupplierBillLine(usage_event_id=event_id,reference=reference,amount=float(value)))
    db.flush()
    date=usage.created_at.replace(tzinfo=timezone.utc) if usage.created_at.tzinfo is None else usage.created_at
    day=date.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    for scope,key in [('session',usage.session_id),('user',f'{usage.user_id}:{day}'),('project',f'all:{day}')]:
        row=db.query(BudgetLedger).filter_by(scope=scope,scope_key=key).first()
        if row is None:
            db.add(BudgetLedger(scope=scope,scope_key=key,spent=0,reserved=0,actual_spent=float(value)))
        else:
            db.execute(update(BudgetLedger).where(BudgetLedger.id==row.id).values(actual_spent=func.coalesce(BudgetLedger.actual_spent,0)+float(value)))
    db.commit()
