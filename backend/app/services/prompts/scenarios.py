"""v0.7 场景配置（版本化 + 审核状态）。

场景由版本化配置驱动；会话创建时只读取 review_status == "approved" 的场景。
本模块是「已审核版本」的唯一事实来源，教练、评估、求助、完成判断、回顾都从这里取。
"""

from app.core.errors import AppError

SERIES = {
    "career": "职场与求职",
    "travel": "旅行与生活",
    "daily": "日常交流",
}

# 每个场景：
# - subgoals：子目标 key 列表，全部 independent 才算完成
# - acceptable_points：评估器判断 independent 的关键词
# - follow_ups：各子目标完成后的确定性追问（mock 教练使用）
# - help_materials：四类求助中「组织」类帮助的逐级材料
# - reference_expressions：参考表达（跟读建议 / 回顾建议句）
# - variations / stop_conditions / completion_criteria：PRD 6.2 要求字段
SCENARIOS: dict[str, dict] = {
    "job_interview": {
        "scene_id": "job_interview",
        "series": "career",
        "label": "英文面试",
        "version": "v1",
        "review_status": "approved",
        "user_role": "求职者",
        "coach_role": "面试官",
        "goal_text": "Introduce one experience and answer one related follow-up question.",
        "goal_zh": "介绍一段经历并回答相关追问",
        "openings": {
            "beginner": "Welcome. Tell me one simple thing about yourself, like your name and what you do.",
            "basic": "Welcome. Tell me about one experience you are proud of.",
            "intermediate": "Welcome. Walk me through one achievement and how you contributed to it.",
        },
        "coach_context": "an English interview coach helping an adult practice a job-interview self-introduction",
        "subgoals": ["intro", "followup"],
        "acceptable_points": ["i have", "experience", "worked", "project", "years", "achieved", "led"],
        "subgoal_standard": {
            "intro": "state one specific past experience (job/project) with concrete detail",
            "followup": "answer the follow-up about that experience with a relevant detail",
        },
        "follow_ups": {
            "intro": ["What was the most challenging part of that experience?", "What did you learn from it?"],
            "followup": ["What was the result of that work?", "How did it affect your team?"],
        },
        "help_materials": {
            "keywords": "experience / project / result",
            "half": "你可以这样开头：'I have experience in...'",
            "full": "I have three years of experience in software development, and I led a project that improved delivery speed.",
            "explain_zh": "面试官在请你介绍一段自己的工作或学习经历，并可能继续追问这段经历的细节。",
            "simple_question": "Tell me about one job or project you did.",
        },
        "reference_expressions": [
            "I have three years of experience in software development.",
            "I led a project that improved delivery speed by thirty percent.",
        ],
        "variations": ["介绍在校经历", "介绍实习经历", "介绍一段项目经历"],
        "stop_conditions": "两个子目标均独立完成，或用户主动结束，或到时收尾",
        "completion_criteria": "介绍一段经历（intro）+ 回答一个相关追问（followup）均独立完成",
    },
    "new_colleague": {
        "scene_id": "new_colleague",
        "series": "career",
        "label": "认识新同事",
        "version": "v1",
        "review_status": "approved",
        "user_role": "新入职员工",
        "coach_role": "同事",
        "coach_persona": "You are a product manager on the product team.",
        "co_complete": {
            "ask_question": ["what about you", "how about you", "what do you", "what's your", "what is your"]
        },
        "goal_text": "Introduce yourself and ask the other person one question.",
        "goal_zh": "完成自我介绍并向对方提出一个问题",
        "openings": {
            "beginner": "Hi, I don't think we've met. Can you tell me your name and what you do?",
            "basic": "Welcome to the team. Can you introduce yourself and tell me one thing you enjoy about your work?",
            "intermediate": "Great to meet you. Tell me a bit about your role, and feel free to ask me one question too.",
        },
        "coach_context": "a friendly new colleague chatting with an adult on their first day at work",
        "subgoals": ["self_intro", "ask_question"],
        "acceptable_points": ["i am", "i work", "my name", "my role", "i do", "what about you", "how about you", "what do you"],
        "subgoal_standard": {
            "self_intro": "state name or role with concrete detail (not just one vague phrase)",
            "ask_question": "ask the other person a question (What about you / What do you do)",
        },
        "follow_ups": {
            "self_intro": ["Nice to meet you. What do you enjoy about your work?"],
            "ask_question": ["Good question! Let me tell you a bit about that."],
        },
        "help_materials": {
            "keywords": "name / role / what about you",
            "half": "可以先说：'I am..., I work on...'，再问 'What about you?'",
            "full": "I am a designer on the product team, and I focus on the mobile app. What about you?",
            "explain_zh": "新同事想认识你，请介绍一下你自己（名字、岗位），也可以反过来问对方一个问题。",
            "simple_question": "What's your name and what do you do?",
        },
        "reference_expressions": [
            "I am a designer on the product team.",
            "What about you? What do you do here?",
        ],
        "variations": ["第一天上班自我介绍", "团队欢迎会", "茶水间偶遇"],
        "stop_conditions": "自我介绍 + 向对方提一个问题均完成，或用户主动结束，或到时收尾",
        "completion_criteria": "自我介绍（self_intro）+ 向对方提问（ask_question）均独立完成",
    },
    "meeting_opinion": {
        "scene_id": "meeting_opinion",
        "series": "career",
        "label": "会议中表达意见",
        "version": "v1",
        "review_status": "approved",
        "user_role": "参会者",
        "coach_role": "会议主持人",
        "goal_text": "State your opinion clearly and respond to one follow-up question.",
        "goal_zh": "清楚说明观点并回应一次追问",
        "openings": {
            "beginner": "In our meeting today, can you share one simple opinion about the project?",
            "basic": "We're discussing the project timeline. What's your opinion, and why do you think so?",
            "intermediate": "The team is split on how to prioritize. What's your view, and how would you respond to a different opinion?",
        },
        "coach_context": "a meeting host inviting an adult to share and defend an opinion in English",
        "subgoals": ["state_opinion", "give_reason"],
        "acceptable_points": ["i think", "in my opinion", "i believe", "we should", "because", "the reason", "i agree", "i disagree"],
        "subgoal_standard": {
            "state_opinion": "state a clear specific opinion/stance (not just 'I agree')",
            "give_reason": "give a reason for the opinion (because...)",
        },
        "follow_ups": {
            "state_opinion": ["Why do you think so?", "Could you explain your reasoning?"],
            "give_reason": ["How would you respond if someone disagreed?"],
        },
        "help_materials": {
            "keywords": "I think / because / reason",
            "half": "可以先说观点：'I think we should...'，再补理由 'because...'",
            "full": "I think we should launch the smaller version first, because it is faster to test and cheaper to fix.",
            "explain_zh": "主持人请你对这个项目发表你的看法，并说明你支持或反对的理由。",
            "simple_question": "What do you think about the plan, and why?",
        },
        "reference_expressions": [
            "I think we should launch the smaller version first.",
            "Because it is faster to test and cheaper to fix.",
        ],
        "variations": ["讨论排期", "讨论优先级", "回应不同意见"],
        "stop_conditions": "观点 + 理由均独立完成，或用户主动结束，或到时收尾",
        "completion_criteria": "表明观点（state_opinion）+ 给出理由（give_reason）均独立完成",
    },
    "hotel_checkin": {
        "scene_id": "hotel_checkin",
        "series": "travel",
        "label": "酒店入住",
        "version": "v1",
        "review_status": "approved",
        "user_role": "住客",
        "coach_role": "前台接待",
        "goal_text": "Confirm your reservation and ask about one service detail.",
        "goal_zh": "确认预订并询问一项服务信息",
        "openings": {
            "beginner": "Welcome to the hotel. Can I have your name, please?",
            "basic": "Hello, checking in. Do you have a reservation under your name?",
            "intermediate": "Welcome. I see your booking for two nights. Is there anything else you'd like to ask about our services?",
        },
        "coach_context": "a hotel front-desk clerk checking in an adult guest in English",
        "subgoals": ["confirm_booking", "ask_service"],
        "acceptable_points": ["reservation", "booking", "my name", "check in", "room", "breakfast", "wifi", "gym", "what time", "where is"],
        "subgoal_standard": {
            "confirm_booking": "confirm reservation (name or nights)",
            "ask_service": "ask about a service (breakfast/wifi/time)",
        },
        "follow_ups": {
            "confirm_booking": ["May I see your ID, please?", "Is it two nights, correct?"],
            "ask_service": ["Sure. Breakfast is from 7 to 10 on the second floor.", "The wifi password is on your key card."],
        },
        "help_materials": {
            "keywords": "reservation / breakfast / wifi",
            "half": "可以先确认：'I have a reservation under...'，再问一项服务",
            "full": "I have a reservation under the name Li. What time does breakfast start?",
            "explain_zh": "前台在确认你的预订信息（姓名/几晚），并会问你有没有需要了解的服务。",
            "simple_question": "Do you have a reservation? What's your name?",
        },
        "reference_expressions": [
            "I have a reservation under the name Li.",
            "What time does breakfast start?",
        ],
        "variations": ["预订姓名确认", "询问早餐时间", "询问 wifi 密码"],
        "stop_conditions": "确认预订 + 询问一项服务均完成，或用户主动结束，或到时收尾",
        "completion_criteria": "确认预订（confirm_booking）+ 询问服务信息（ask_service）均独立完成",
    },
    "restaurant_order": {
        "scene_id": "restaurant_order",
        "series": "travel",
        "label": "餐厅点餐",
        "version": "v1",
        "review_status": "approved",
        "user_role": "顾客",
        "coach_role": "服务员",
        "goal_text": "Complete your order and handle one follow-up question.",
        "goal_zh": "完成点餐并处理一个补充问题",
        "openings": {
            "beginner": "Hi, are you ready to order? What would you like to drink?",
            "basic": "Good evening. What would you like to order today?",
            "intermediate": "Welcome. Can I take your order, or would you like a moment to look at the menu?",
        },
        "coach_context": "a restaurant waiter taking an adult customer's order in English",
        "subgoals": ["place_order", "handle_followup"],
        "acceptable_points": ["i would like", "i will have", "order", "please", "menu", "no sugar", "spicy", "no onions", "no ice"],
        "subgoal_standard": {
            "place_order": "state what to order",
            "handle_followup": "answer a follow-up (drink/preference)",
        },
        "follow_ups": {
            "place_order": ["Would you like anything to drink with that?"],
            "handle_followup": ["Any allergies or preferences I should know about?"],
        },
        "help_materials": {
            "keywords": "I would like / please",
            "half": "点餐可以这样：'I would like...'，特殊要求补 'no...' 或 'without...'",
            "full": "I would like the grilled chicken, please. No spicy food, please.",
            "explain_zh": "服务员在请你点餐，可能还会追问你的特殊要求（不要辣、饮料等）。",
            "simple_question": "What would you like to order?",
        },
        "reference_expressions": [
            "I would like the grilled chicken, please.",
            "No spicy food, please.",
        ],
        "variations": ["点主菜", "点饮料", "说明忌口"],
        "stop_conditions": "完成点餐 + 处理补充问题均完成，或用户主动结束，或到时收尾",
        "completion_criteria": "完成点餐（place_order）+ 处理补充问题（handle_followup）均独立完成",
    },
    "new_friend": {
        "scene_id": "new_friend",
        "series": "daily",
        "label": "认识新朋友",
        "version": "v1",
        "review_status": "approved",
        "user_role": "新朋友",
        "coach_role": "刚认识的朋友",
        "goal_text": "Introduce yourself and keep a simple conversation going for two rounds.",
        "goal_zh": "介绍自己并延续两轮简单交流",
        "openings": {
            "beginner": "Hi, nice to meet you. What's your name?",
            "basic": "Hey, nice to meet you. Tell me a bit about yourself — what do you do?",
            "intermediate": "Nice to meet you. What do you like to do in your free time?",
        },
        "coach_context": "a friendly new acquaintance making small talk with an adult in English",
        "coach_persona": "You work at a local café.",
        "subgoals": ["round1", "round2"],
        "acceptable_points": ["my name", "i am", "i do", "i work", "i like", "i enjoy", "in my free time"],
        "subgoal_standard": {
            "round1": "introduce self (name/job/hobby)",
            "round2": "keep conversation going with a second round",
        },
        "follow_ups": {
            "round1": ["Nice. What do you like to do in your free time?"],
            "round2": ["That sounds interesting. Tell me a bit more about it."],
        },
        "help_materials": {
            "keywords": "name / I like / free time",
            "half": "可以先说 'I am...'，再聊一件你喜欢做的事",
            "full": "I am Alex, and I work in marketing. In my free time I like hiking.",
            "explain_zh": "新朋友想认识你，请介绍一下你自己（工作/爱好），让对话继续下去。",
            "simple_question": "What do you do? What do you like?",
        },
        "reference_expressions": [
            "I am Alex, and I work in marketing.",
            "In my free time I like hiking.",
        ],
        "variations": ["初次见面", "聚会闲聊", "介绍兴趣爱好"],
        "stop_conditions": "两轮简单交流均完成，或用户主动结束，或到时收尾",
        "completion_criteria": "两轮交流（round1 + round2）均独立完成，一段长回答不能绕过两轮要求",
    },
    "hobby": {
        "scene_id": "hobby",
        "series": "daily",
        "label": "聊兴趣爱好",
        "version": "v1",
        "review_status": "approved",
        "user_role": "聊天对象",
        "coach_role": "朋友",
        "goal_text": "Describe one hobby and explain why you like it.",
        "goal_zh": "描述一项兴趣并说明喜欢的原因",
        "openings": {
            "beginner": "What's one hobby you enjoy?",
            "basic": "Tell me about a hobby you enjoy, and what you like about it.",
            "intermediate": "What's a hobby you're really into, and why do you enjoy it so much?",
        },
        "coach_context": "a friend asking an adult about their hobbies in English",
        "subgoals": ["describe_hobby", "explain_reason"],
        "acceptable_points": ["i like", "i enjoy", "i love", "hobby", "because", "it helps", "relax", "fun"],
        "subgoal_standard": {
            "describe_hobby": "name a specific hobby",
            "explain_reason": "explain why (because...)",
        },
        "follow_ups": {
            "describe_hobby": ["What do you like about it?", "How did you get into it?"],
            "explain_reason": ["How often do you do it?"],
        },
        "help_materials": {
            "keywords": "I like / because",
            "half": "先说自己喜欢什么：'I like...'，再补原因 'because...'",
            "full": "I like painting in my free time, because it helps me relax.",
            "explain_zh": "对方在问你的兴趣爱好，请说说你喜欢做什么，以及为什么喜欢。",
            "simple_question": "What hobby do you like, and why?",
        },
        "reference_expressions": [
            "I like painting in my free time.",
            "Because it helps me relax.",
        ],
        "variations": ["运动类爱好", "艺术类爱好", "阅读/观影"],
        "stop_conditions": "描述兴趣 + 说明原因均完成，或用户主动结束，或到时收尾",
        "completion_criteria": "描述兴趣（describe_hobby）+ 说明原因（explain_reason）均独立完成",
    },
}


def get_approved_scenes() -> list[dict]:
    """返回已审核场景，按系列稳定顺序。"""
    order = {"career": 0, "travel": 1, "daily": 2}
    scenes = [s for s in SCENARIOS.values() if s["review_status"] == "approved"]
    return sorted(scenes, key=lambda s: (order.get(s["series"], 9), s["scene_id"]))


def get_scene(scene_id: str) -> dict:
    """取单个已审核场景；不存在或未审核则报错。"""
    scene = SCENARIOS.get(scene_id)
    if scene is None or scene["review_status"] != "approved":
        raise AppError("SCENE_NOT_FOUND", "场景不存在或未开放", status_code=404)
    return scene
