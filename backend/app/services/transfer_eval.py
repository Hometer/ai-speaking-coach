"""换题新条件的独立语义核对；不以词重合生成迁移成功。"""
import json

from app.core.config import settings
from app.core.errors import AppError
from app.core.jsonutil import extract_json_object
from app.services.generation_call import call_generation


def verify_new_condition(question: str, original: str, answer: str) -> bool | None:
    """True=明确满足新条件；False=明确未满足；None=无法判断。Mock 不生成真实语义结论。"""
    if settings.provider_mode != "real":
        return None
    try:
        response = call_generation(
            api_key=settings.dashscope_api_key,
            model=settings.model_llm,
            messages=[
                {"role": "system", "content": (
                    "You evaluate transfer in an adult English speaking exercise. "
                    "Judge whether the answer addresses the NEW condition in the question, rather than merely sharing words with the old sentence. "
                    "Return only JSON with boolean fields answers_new_condition, relevant, and not_copy. "
                    "If evidence is unclear, set at least one field false. Do not infer facts absent from the answer."
                )},
                {"role": "user", "content": json.dumps({"new_condition": question, "old_sentence": original, "answer": answer}, ensure_ascii=False)},
            ],
        )
        content = response.output.choices[0].message.content
        data = extract_json_object(content)
        if not isinstance(data, dict) or any(type(data.get(k)) is not bool for k in ("answers_new_condition", "relevant", "not_copy")):
            return None
        return bool(data["answers_new_condition"] and data["relevant"] and data["not_copy"])
    except (AppError, AttributeError, IndexError, TypeError, ValueError):
        return None
