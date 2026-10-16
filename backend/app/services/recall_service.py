"""同一套队列证据编排：回忆后换题，新题成功才升级间隔与掌握。"""
from app.core.config import settings
from sqlalchemy import update
from app.core.errors import AppError
from app.models.entities import RecallEvidence, Session, ReviewQueueItem
from app.services.budget import BudgetGuard
from app.services.generation_call import reset_usage_attempts
from app.services.provider_usage import record_llm_attempts
from app.services.recall_judge import judge_recall, judge_transfer
from app.services.semantic_eval import verify_meaning
from app.services.transfer_eval import verify_new_condition
from app.services.recall_tasks import question_for


def evaluate(db, item, answer, input_type, session_id='', request_key='', validate=None):
    source = db.get(Session, item.source_session_id)
    version, helped, status = item.question_version, item.help_shown, item.status
    stage = item.attempt_stage or 'recall'
    question = item.question_text if stage == 'transfer' else item.meaning_zh
    guard = None
    if settings.provider_mode == 'real' and input_type == 'voice' and not item.help_shown:
        guard = BudgetGuard(db)
        guard.reserve(settings.nominal_cost_per_turn, session_id or item.source_session_id, item.user_id)
        reset_usage_attempts()
        guard.mark_called()
    try:
        if stage == 'transfer':
            verified = verify_new_condition(question, item.suggestion_sentence, answer) if input_type == 'voice' and not item.help_shown else None
            result = judge_transfer(answer, item.suggestion_sentence, input_type, item.help_shown, verified)
            if result == 'transfer':
                result = 'independent'  # 保持原 API 的成功枚举，证据另存 stage=transfer。
        else:
            verified = verify_meaning(item.suggestion_sentence, answer) if input_type == 'voice' and not item.help_shown else None
            result = judge_recall(answer, item.suggestion_sentence, input_type, item.help_shown, verified)
            if result == 'independent':
                result = 'recall_completed'
    finally:
        if guard:
            guard.settle(settings.nominal_cost_per_turn, session_id or item.source_session_id, item.user_id)
            record_llm_attempts(db, item.user_id, session_id or item.source_session_id)
    # 费用/原始用量先保存；业务证据必须在全部外部调用之后通过状态核对，不能被账本 commit 提前保存。
    if validate:
        validate()
    changed = db.execute(update(ReviewQueueItem).where(ReviewQueueItem.id == item.id,
        ReviewQueueItem.question_version == version, ReviewQueueItem.status == status,
        ReviewQueueItem.help_shown == helped, ReviewQueueItem.attempt_stage == stage
    ).values(question_version=version + 1))
    if changed.rowcount != 1:
        db.rollback()
        raise AppError('STALE_OPERATION', '复练内容已改变，已忽略迟到结果', 409)
    db.refresh(item)
    db.add(RecallEvidence(user_id=item.user_id, queue_item_id=item.id, question_version=version,
        stage=stage, question=question, input_type=input_type, help_shown=helped, result=result, request_key=request_key))
    if result == 'recall_completed':
        item.attempt_stage = 'transfer'
        item.question_text = question_for(source.scene_id if source else '')
    return result
