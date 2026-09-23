"""教练追问自然度 A/B：三版提示词并列对比。

- v1（优化前）、v2（第一轮 Got it 版）、v4（第二轮 intent 版）三版系统提示词，
  在相同 context、相同用户输入上各跑一遍，输出并列表。
- 不打印密钥。
- ROUND2_CASES 为第二轮验收输入，刻意不与任何提示词示例、第一轮输入重复。
- 用法：cd backend && python scripts/ab_followup.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.services.prompts.coach import (
    COACH_DECISION_FORMAT,
    COACH_SYSTEM_PROMPT_VERSION,
    coach_system_prompt,
)
from app.services.prompts.scenarios import get_scene

# ---- 三版系统提示词（v1/v2 为历史版本，固化在此用于对比；v4 取当前生产代码）----

_ACTIONS = (
    "You are responsible for teaching decisions only. You never declare whether the user has "
    "'mastered' anything; a separate evaluator judges completion (independent / hinted / "
    "incomplete / insufficient evidence).\n"
    "Stay inside the goal. Choose exactly ONE teaching action per turn:\n"
    "- follow_up: ask ONE relevant follow-up question about what the user said.\n"
    "- hint: when the user is stuck or gave a very short answer, give a short English hint "
    "(and optionally one brief Chinese hint).\n"
    "- suggest_practice: suggest a short sentence for the user to repeat (only when useful).\n"
    "- recheck: after a hint or practice, ask a similar-but-different question to verify "
    "the user can express it independently.\n"
)

_V1_FORMAT = (
    "Output ONLY a JSON object, no markdown, no extra text, with exactly these fields:\n"
    '{"action_type": "follow_up", "reply_text": "<short English reply>", '
    '"follow_up_question": "<optional question>", "practice_sentence": "<optional, only for suggest_practice>", '
    '"needs_evaluation": true}\n'
    'action_type must be one of: follow_up, hint, suggest_practice, recheck. '
    'Do NOT use numbered lists or other formatting.'
)


def v1_system(scene: str) -> str:
    return (
        f"You are {scene}. You are a patient, friendly partner, NOT a grader.\n"
        + _ACTIONS
        + "Never mock, never give scores, never compare the user negatively. Keep sentences short."
    )


def v2_system(scene: str) -> str:
    return (
        f"You are {scene}. You are a patient, friendly partner, NOT a grader.\n"
        + _ACTIONS
        + "\n"
        "Follow-up rules (apply to follow_up ONLY; hint, suggest_practice and recheck keep their "
        "short reply with no acknowledgment):\n"
        "1. Acknowledge first, then ask. Before the question, briefly react to a specific detail "
        "the user just said (restate it or respond to it). Prefer a neutral opener ('Got it', "
        "'Sure', 'Okay', 'Right', 'So you...') over praise words. Ground the reply in the user's "
        "own words; never invent facts about scene items and never give empty, unfounded praise "
        "(no 'great choice', no 'it is very popular').\n"
        "2. Put the COMPLETE reply in reply_text: the acknowledgment plus the question, written out "
        "in full. The frontend and the voice read only reply_text, so never leave the question "
        "only in follow_up_question.\n"
        "3. Keep it short: at most TWO sentences, exactly ONE question mark, and match the user's "
        "level (beginner: simpler words and shorter; intermediate: slightly more natural flow).\n"
        "4. follow_ups in the context are scene guardrails and question directions. You may use "
        "one as-is, but prefer to naturally rephrase it around what the user actually said.\n"
        "\n"
        "Examples (react to the user's own words, then ask):\n"
        "[transactional] user: \"I have a reservation under the name Li.\"\n"
        "stiff: \"May I see your ID, please?\"\n"
        "natural: \"Got it, a reservation under Li. Could I see your ID, please?\"\n"
        "[social/workplace] user: \"I think we should launch the smaller version first.\"\n"
        "stiff: \"Why do you think so?\"\n"
        "natural: \"So you'd go with the smaller version first. Why do you think so?\"\n"
        "Anti-example: user said \"I would like the grilled chicken, please.\" Do NOT say \"Great "
        "choice, the grilled chicken is very popular\" (invented fact + unfounded praise). Say: "
        "\"Sure, the grilled chicken. Would you like anything to drink with that?\"\n"
        "\n"
        "Never mock, never give scores, never compare the user negatively. Keep sentences short."
    )


def v4_system(scene: str) -> str:
    return coach_system_prompt(scene)


SYSTEMS = {
    "v1优化前": (v1_system, _V1_FORMAT),
    "v2第一轮": (v2_system, COACH_DECISION_FORMAT),
    "v4第二轮": (v4_system, COACH_DECISION_FORMAT),
}

# ---- 第二轮验收输入（scene_id, 用户回答, eval_result, subgoal或None, 覆盖类型）----
# 覆盖：反问 / 同场景不同表达 / 极短回答 / 两个信息点 / 语法错误但意思清楚 / 已回答预设追问
ROUND2_CASES = [
    ("new_colleague", "I just joined the sales team. What about you?", "incomplete", None, "用户主动反问"),
    ("hotel_checkin", "Hi, I have a booking for two nights.", "incomplete", None, "同一场景不同表达"),
    ("restaurant_order", "Just water, please.", "incomplete", None, "极短回答"),
    ("hobby", "I like swimming, and I go every Saturday morning.", "incomplete", None, "两个信息点"),
    ("job_interview", "I have three year experience in software, I work with team.", "incomplete", None, "语法错误但意思清楚"),
    ("meeting_opinion", "I think we should move the deadline to Friday because the team needs more time.", "independent", "give_reason", "已回答预设追问"),
    ("new_friend", "I'm a teacher. What do you do?", "incomplete", None, "用户反问（日常场景）"),
    ("hotel_checkin", "Yes, for two nights.", "incomplete", None, "极短回答/不同表达"),
]


def call(system: str, user: str) -> tuple[str, float]:
    import dashscope

    dashscope.api_key = settings.dashscope_api_key
    t0 = time.perf_counter()
    resp = dashscope.Generation.call(
        model=settings.model_llm,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        result_format="message",
    )
    dt = time.perf_counter() - t0
    if getattr(resp, "status_code", None) != 200:
        return f"[ERROR {getattr(resp, 'code', '')} {getattr(resp, 'message', '')}]", dt
    return resp.output.choices[0].message.content.strip(), dt


def build_context(scene_id: str, answer: str, eval_result: str, subgoal: str | None) -> dict:
    sc = get_scene(scene_id)
    return {
        "goal": sc["goal_text"],
        "difficulty": "normal",
        "level": "basic",
        "scene_context": sc["coach_context"],
        "scene_id": scene_id,
        "follow_ups": sc["follow_ups"],
        "reference_expressions": sc["reference_expressions"],
        "current_question": sc["openings"]["basic"],
        "current_answer": answer,
        "input_type": "text",
        "hint_used": False,
        "eval_result": eval_result,
        "subgoal": subgoal or sc["subgoals"][0],
        "history": [],
        "rechecked": False,
        "memories": [],
    }


def extract_reply(text: str) -> str:
    try:
        data = json.loads(text)
        action = data.get("action_type", "")
        reply = str(data.get("reply_text", "")).strip()
        return f"[{action}] {reply}" if reply else text
    except Exception:
        return text


def main() -> None:
    rows = []
    timings: dict[str, float] = {k: 0.0 for k in SYSTEMS}
    for scene_id, answer, eval_result, subgoal, kind in ROUND2_CASES:
        sc = get_scene(scene_id)
        ctx = build_context(scene_id, answer, eval_result, subgoal)
        user_msg = (
            "Session context as JSON:\n"
            + json.dumps(ctx, ensure_ascii=False)
            + "\nChoose the next teaching action. The follow-up must be based on what the user actually said."
        )
        row = {"scene": sc["label"], "answer": answer, "kind": kind, "out": {}}
        for name, (builder, fmt) in SYSTEMS.items():
            raw, dt = call(builder(sc["coach_context"]) + "\n" + fmt, user_msg)
            row["out"][name] = extract_reply(raw)
            timings[name] += dt
        rows.append(row)
        print(f"[{sc['label']}] {kind} done", file=sys.stderr)

    print("\n=== 逐条对照 ===")
    for r in rows:
        print(f"\n[{r['scene']}]（{r['kind']}）\n  用户：{r['answer']}")
        for name in SYSTEMS:
            print(f"  {name}：{r['out'][name]}")

    print("\n=== Markdown 表 ===")
    print("| 场景 | 覆盖类型 | 用户回答 | " + " | ".join(SYSTEMS) + " |")
    print("| --- | --- | --- | " + " | ".join("---" for _ in SYSTEMS) + " |")
    for r in rows:
        outs = " | ".join(r["out"][n] for n in SYSTEMS)
        print(f"| {r['scene']} | {r['kind']} | {r['answer']} | {outs} |")

    print("\n=== 时延汇总（未含 TTS）===")
    for name, t in timings.items():
        print(f"{name}：{t:.1f}s（{len(ROUND2_CASES)} 次调用）")


if __name__ == "__main__":
    main()
