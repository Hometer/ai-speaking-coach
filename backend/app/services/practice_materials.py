"""同一句建议共享中文意图和语块位置；未知材料不套场景模板。"""
import re
from pydantic import ValidationError
from app.core.errors import AppError
from app.schemas.evaluation import PracticeMaterial

VERSION = 'practice-material-v1'

# 已审核固定材料；只补精确匹配的旧表达，不从语法解释猜原意。
# sentence, meaning, scenes, replaceable span, replacement; 示例只替换明确片段。
_ROWS = [
 ('I am working.', '我正在工作。', ['job_interview','new_colleague'], 'working', 'learning'),
 ('Yesterday I went to work.', '我昨天去上班了。', ['job_interview','new_colleague'], 'Yesterday', 'Last Monday'),
 ('She has a book.', '她有一本书。', ['new_friend','hobby'], 'a book', 'a notebook'),
 ('Hello.', '你好。', ['job_interview','new_colleague','meeting_opinion','hotel_checkin','restaurant_order','new_friend','hobby'], 'Hello', 'Hi'),
 ('Hi.', '你好。', ['new_colleague','hotel_checkin','restaurant_order','new_friend','hobby'], 'Hi', 'Hello'),
 ('Yesterday I booked a room for two nights.', '我昨天预订了一个房间，住两晚。', ['hotel_checkin'], 'two nights', 'three nights'),
 ('I booked a room for two nights', '我预订了一个房间，住两晚。', ['hotel_checkin'], 'two nights', 'three nights'),
 ('I booked a room', '我预订了一个房间。', ['hotel_checkin'], 'a room', 'a double room'),
 ('I have three years of experience in software development.', '我有三年软件开发经验。', ['job_interview'], 'software development', 'product design'),
 ('I led a project that improved delivery speed by thirty percent.', '我带领一个项目，将交付速度提高了百分之三十。', ['job_interview'], 'thirty percent', 'twenty percent'),
 ('I am a designer on the product team.', '我是产品团队的设计师。', ['new_colleague','job_interview'], 'a designer', 'a developer'),
 ('What about you? What do you do here?', '你呢？你在这里做什么工作？', ['new_colleague'], 'here', 'on this team'),
 ('I think we should launch the smaller version first.', '我认为我们应该先发布较小的版本。', ['meeting_opinion'], 'the smaller version', 'the basic version'),
 ('Because it is faster to test and cheaper to fix.', '因为这样测试更快，修复成本更低。', ['meeting_opinion'], 'faster to test', 'easier to test'),
 ('I have a reservation under the name Li.', '我有一份以李的名字登记的预订。', ['hotel_checkin'], 'Li', 'Wang'),
 ('What time does breakfast start?', '早餐几点开始？', ['hotel_checkin'], 'breakfast', 'dinner'),
 ('I would like the grilled chicken, please.', '请给我烤鸡。', ['restaurant_order'], 'the grilled chicken', 'the grilled fish'),
 ('No spicy food, please.', '请不要放辣。', ['restaurant_order'], 'spicy food', 'nuts'),
 ('I am Alex, and I work in marketing.', '我叫亚历克斯，从事市场营销工作。', ['new_friend'], 'marketing', 'design'),
 ('In my free time I like hiking.', '闲暇时我喜欢徒步。', ['new_friend','hobby'], 'hiking', 'reading'),
 ('I like painting in my free time.', '我喜欢在闲暇时画画。', ['hobby','new_friend'], 'painting', 'reading'),
 ('Because it helps me relax.', '因为这能让我放松。', ['hobby','new_friend'], 'relax', 'focus'),
]


def _norm(value):
    return ' '.join(value.strip().rstrip('.').casefold().split())


def validated(sentence, value):
    try:
        material = value if isinstance(value, PracticeMaterial) else PracticeMaterial.model_validate(value)
    except (ValidationError, TypeError, ValueError):
        return None
    # 脱稿阶段只能给中文意图，不能把英文答案或纠错说明当提示。
    if re.search('[A-Za-z]', material.meaning_zh) or not re.search('[\u4e00-\u9fff]', material.meaning_zh):
        return None
    if not 1 <= len(sentence.split()) <= 35 or material.chunk_text not in sentence or not re.search('[A-Za-z]', material.chunk_text):
        return None
    if sentence.count(material.replaceable_text) != 1 or material.replacement == material.replaceable_text:
        return None
    return material


def material_for(imp, scene_id, required=True):
    sentence = imp.get('practice_sentence') or ''
    material = validated(sentence, imp.get('practice_material'))
    if material is None:
        for target, meaning, scenes, span, replacement in _ROWS:
            if _norm(sentence) == _norm(target):
                # 实际参考的大小写/标点保持原样；中文及槽位只来自固定审核表。
                actual_span = re.search(re.escape(span), sentence, re.I)
                material = validated(sentence, dict(meaning_zh=meaning, context_zh='练习这句话所表达的意思，替换示例仅供练习。',
                    scene_ids=scenes, chunk_text=(sentence[:actual_span.start()].strip(' ,.!?') or sentence[actual_span.end():].strip(' ,.!?') or actual_span.group()) if actual_span else sentence, replaceable_text=actual_span.group() if actual_span else span, replacement=replacement))
                break
    if material is None or scene_id not in material.scene_ids:
        if required:
            raise AppError('MATERIAL_NOT_AVAILABLE', '这条建议的复练材料尚未准备好，仍可查看原句与纠错说明', 409)
        return None
    return material


def annotate(imp, scene_id):
    material = material_for(imp, scene_id, required=False)
    return {**imp, 'practice_available': material is not None,
        'practice_material': material.model_dump() if material else None,
        'material_version': VERSION}


def existing_material(db, source_session_id, source_turn_id, sentence):
    import json
    from app.models.entities import Session
    source = db.get(Session, source_session_id)
    if source is None:
        return None
    try:
        review = json.loads(source.review or '{}')
    except (TypeError, ValueError):
        return None
    imp = next((i for i in review.get('improvements', []) if i.get('turn_id') == source_turn_id), {'practice_sentence': sentence})
    if review.get('stale') or imp.get('disputed') or _norm(imp.get('practice_sentence', '')) != _norm(sentence):
        return None
    return material_for(imp, source.scene_id, required=False)
