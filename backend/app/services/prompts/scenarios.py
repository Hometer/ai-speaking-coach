"""v0.7 场景配置（版本化 + 审核状态）。

场景由版本化配置驱动；会话创建时只读取 review_status == "approved" 的场景。
本模块是「已审核版本」的唯一事实来源，前端列表与后端开场都从这里取。
"""

from app.core.errors import AppError

SERIES = {
    "career": "职场与求职",
    "travel": "旅行与生活",
    "daily": "日常交流",
}

# scene_id -> 配置。goal_text 用于评估器与教练（英文，即完成条件）。
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
    },
    "new_colleague": {
        "scene_id": "new_colleague",
        "series": "career",
        "label": "认识新同事",
        "version": "v1",
        "review_status": "approved",
        "user_role": "新入职员工",
        "coach_role": "同事",
        "goal_text": "Introduce yourself and ask the other person one question.",
        "goal_zh": "完成自我介绍并向对方提出一个问题",
        "openings": {
            "beginner": "Hi, I don't think we've met. Can you tell me your name and what you do?",
            "basic": "Welcome to the team. Can you introduce yourself and tell me one thing you enjoy about your work?",
            "intermediate": "Great to meet you. Tell me a bit about your role, and feel free to ask me one question too.",
        },
        "coach_context": "a friendly new colleague chatting with an adult on their first day at work",
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
            "basic": "Hey, I'm Alex. Tell me a bit about yourself — what do you do?",
            "intermediate": "Nice to meet you. What do you like to do in your free time?",
        },
        "coach_context": "a friendly new acquaintance making small talk with an adult in English",
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
