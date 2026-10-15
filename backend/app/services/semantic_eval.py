"""复述语义校验：真实模式结构化评估，Mock 只接受固定离线样例。"""
import json
import re
from app.core.config import settings
from app.core.errors import AppError
from app.core.jsonutil import extract_json_object
from app.services.generation_call import call_generation


def verify_meaning(target: str, answer: str) -> bool | None:
    if not target.strip() or not answer.strip():
        return None
    if settings.provider_mode != 'real':
        # 仅用于离线编排。未知表达保持待判断，绝不以词重合推断成功。
        norm = lambda s: re.sub(r"[^a-z0-9 ]", '', s.lower()).strip()
        return True if norm(target) == norm(answer) else None
    try:
        response = call_generation(api_key=settings.dashscope_api_key, model=settings.model_llm,
            messages=[{'role':'system','content':
                'Judge whether an English answer conveys the complete intended meaning of the target. '
                'Accept synonyms and natural paraphrases. Reject contradictions, negation changes, unrelated vocabulary lists and missing essential conditions. '
                'Return ONLY JSON: {"same_meaning":boolean,"sufficient":boolean}. Unclear evidence must be false.'},
                {'role':'user','content':json.dumps({'target':target,'answer':answer})}])
        data = extract_json_object(response.output.choices[0].message.content)
        if not isinstance(data, dict) or any(type(data.get(k)) is not bool for k in ('same_meaning','sufficient')):
            return None
        return data['same_meaning'] and data['sufficient']
    except (AppError, AttributeError, IndexError, TypeError, ValueError):
        return None
