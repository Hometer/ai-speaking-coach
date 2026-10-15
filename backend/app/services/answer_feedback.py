"""原回合语言反馈；不使用场景固定参考句冒充个性化诊断。"""
import re
from pydantic import ValidationError
from app.schemas.evaluation import AnswerFeedback

VERSION = "answer-feedback-v1"


def grounded(answer: str, values) -> list[AnswerFeedback]:
    if not isinstance(values, list):
        return []
    result = []
    for value in values[:2]:
        try:
            item = value if isinstance(value, AnswerFeedback) else AnswerFeedback.model_validate(value)
        except (ValidationError, TypeError, ValueError):
            continue
        if not item.facts_preserved or item.source_quote not in answer:
            continue
        if item.practice_sentence.strip().casefold() == answer.strip().casefold():
            continue
        if set(re.findall(r"\d+(?:[.,]\d+)*%?", item.practice_sentence)) - set(re.findall(r"\d+(?:[.,]\d+)*%?", answer)):
            continue
        # 拒绝新出现的显式人名/地名；句首的一般大写不视为专有名词。
        names = re.findall(r"(?<![.!?]\s)(?<=\s)[A-Z][a-z]+", item.practice_sentence)
        if any(name.casefold() not in answer.casefold() for name in names):
            continue
        result.append(item)
    return result


def mock_feedback(answer: str) -> list[AnswerFeedback]:
    fixtures = {
        "just working": ("I am working.", "如果想把这段原话说成完整陈述句，可补出主语 I 和 am。"),
        "heii I": ("Hello.", "这是一条固定离线拼写样例，将问候词整理为 Hello；真实意思仍需核对。"),
        "heiil": ("Hi.", "这是一条固定离线拼写样例，将问候词整理为 Hi；真实意思仍需核对。"),
        "Yesterday I go to work.": ("Yesterday I went to work.", "Yesterday 表示过去，这里的 go 应使用过去式 went。"),
        "She have a book.": ("She has a book.", "主语 She 是第三人称单数，这里的 have 应使用 has。"),
    }
    fixture = fixtures.get(answer)
    if not fixture:
        return []
    sentence, explanation = fixture
    return grounded(answer, [{"issue_type":"grammar", "source_quote":answer,
        "explanation_cn":explanation,"practice_sentence":sentence,"facts_preserved":True}])
