"""教练追问自然度 A/B 第三轮：v4（旧）vs v5（新）。

- v4 = 旧生产提示词（已固化在此，便于对比，不依赖当前代码）。
- v5 = 当前生产提示词（COACH_SYSTEM_PROMPT_VERSION）。
- v4 用旧上下文（含 follow_ups / reference_expressions），v5 用新上下文
  （含 coach_persona、不含示例材料），两者共同反映「修复前 vs 修复后」。
- 每条输入：v5 × 3 次、v4 × 1 次；记录原始回复、action_type、时延。
- 不打印密钥。

用法：
  cd backend && python scripts/ab_round3_v5.py            # 真实调用（需先确认费用）
  cd backend && python scripts/ab_round3_v5.py --dry-run  # 只打印提示词与上下文，不调用模型
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

# ---- 旧 v4 提示词（固化在此，与 v5 对照）----

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


def v4_system(scene_context: str) -> str:
    return (
        f"You are {scene_context}. You are a patient, friendly partner, NOT a grader.\n"
        + _ACTIONS
        + "\n"
        "Follow-up rules (apply to follow_up ONLY; hint, suggest_practice and recheck keep their "
        "short reply with no acknowledgment):\n"
        "1. Respond naturally to the user's communicative intent.\n"
        "2. If the user asked YOU a question, answer it briefly first, then continue.\n"
        "3. Otherwise, use a short role-appropriate acknowledgment only when it helps; skip it "
        "when it adds nothing.\n"
        "4. Do NOT mechanically repeat or paraphrase the user's full sentence.\n"
        "5. Avoid repeatedly using the same opener (e.g. 'Got it', 'Okay', 'Sure'). Vary how you open.\n"
        "6. Use follow_ups in the context as scene guardrails and intent directions, not scripts to copy.\n"
        "7. Ask at most ONE relevant follow-up question.\n"
        "8. Keep the complete reply in reply_text and within TWO short sentences.\n"
        "9. Never invent facts, never give unsupported praise, scores, or completion judgments.\n"
        "\n"
        "Examples (do not reuse these user lines as your reply):\n"
        "[transactional] user: \"Can I get the pasta without onions, please?\"\n"
        "mechanical (repeats the user's words): \"Got it, pasta without onions. Anything to drink?\"\n"
        "natural: \"No problem. Anything to drink?\"\n"
        "[social] user: \"I visited the new museum last weekend.\"\n"
        "mechanical (repeats the user's words): \"Got it, you visited the new museum last weekend. Do you go often?\"\n"
        "natural: \"Sounds interesting. Do you go often?\"\n"
        "[the user asked you a question] user: \"I live nearby. How about you?\"\n"
        "natural: \"I live across town. What do you like about this neighborhood?\"\n"
        "\n"
        "Never mock, never give scores, never compare the user negatively. Keep sentences short."
    )


def v5_system(scene_context: str, coach_persona: str) -> str:
    return coach_system_prompt(scene_context, coach_persona)


# ---- 第三轮验收输入（scene_id, 用户回答, eval_result, subgoal或None, 覆盖类型）----
# 覆盖：6 条不同措辞的反问、3 条示例材料泄露触发、3 条普通输入，另含极短/语法错误/两个信息点。
ROUND3_CASES = [
    # 用户直接反问（6 条不同措辞）
    ("new_colleague", "I just joined the sales team. What about you?", "incomplete", None, "反问"),
    ("new_friend", "I'm a teacher. What do you do?", "incomplete", None, "反问"),
    ("new_colleague", "Nice to meet you. I'm in HR. How about you?", "incomplete", None, "反问"),
    ("new_friend", "I work in finance. And you?", "incomplete", None, "反问"),
    ("new_colleague", "I'm a designer. What's your role?", "incomplete", None, "反问"),
    ("hobby", "I like hiking. What about you? Do you like it too?", "incomplete", None, "反问"),
    # 可能触发示例材料泄露（3 条）
    ("hotel_checkin", "Hi, I have a booking for two nights.", "incomplete", None, "泄露触发"),
    ("job_interview", "I have three year experience in software, I work with team.", "incomplete", None, "泄露触发"),
    ("new_friend", "My name is Anna and I just moved here.", "incomplete", None, "泄露触发"),
    # 普通事务/社交/职场输入（3 条）
    ("restaurant_order", "Just water, please.", "incomplete", None, "普通（极短）"),
    ("meeting_opinion", "I think we should launch the smaller version first.", "incomplete", None, "普通（职场）"),
    ("hobby", "I like swimming, and I go every Saturday morning.", "incomplete", None, "普通（两个信息点）"),
    # 其余边界情况
    ("job_interview", "I have two year experience in sales and I am good with people.", "incomplete", None, "语法错误+两信息点"),
    ("hotel_checkin", "Yes, for two nights.", "incomplete", None, "极短"),
]


def build_old_context(scene_id: str, answer: str, eval_result: str, subgoal: str | None) -> dict:
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


def build_new_context(scene_id: str, answer: str, eval_result: str, subgoal: str | None) -> dict:
    sc = get_scene(scene_id)
    return {
        "goal": sc["goal_text"],
        "difficulty": "normal",
        "level": "basic",
        "scene_context": sc["coach_context"],
        "scene_id": scene_id,
        "coach_persona": sc.get("coach_persona", ""),
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


def user_message(context: dict) -> str:
    return (
        "Session context as JSON:\n"
        + json.dumps(context, ensure_ascii=False)
        + "\nChoose the next teaching action. The follow-up must be based on what the user actually said."
    )


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


def parse(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except Exception:
        return {"raw": raw, "action_type": "?", "reply_text": raw}
    data["raw"] = raw
    return data


def main() -> None:
    dry = "--dry-run" in sys.argv
    print(f"=== 第三轮 A/B：v4 旧 vs v5 新（版本 {COACH_SYSTEM_PROMPT_VERSION}）===")
    print(f"共 {len(ROUND3_CASES)} 条输入，v5 × 3 + v4 × 1 = {len(ROUND3_CASES) * 4} 次调用\n")

    if dry:
        scene_id, answer, eval_result, subgoal, kind = ROUND3_CASES[0]
        sc = get_scene(scene_id)
        print("---- v4 系统提示词（旧）----")
        print(v4_system(sc["coach_context"]) + "\n" + COACH_DECISION_FORMAT)
        print("\n---- v5 系统提示词（新）----")
        print(v5_system(sc["coach_context"], sc.get("coach_persona", "")) + "\n" + COACH_DECISION_FORMAT)
        print("\n---- 旧上下文（v4 用）----")
        print(json.dumps(build_old_context(scene_id, answer, eval_result, subgoal), ensure_ascii=False, indent=2))
        print("\n---- 新上下文（v5 用）----")
        print(json.dumps(build_new_context(scene_id, answer, eval_result, subgoal), ensure_ascii=False, indent=2))
        return

    timings: dict[str, float] = {"v4": 0.0, "v5": 0.0}
    counts: dict[str, int] = {"v4": 0, "v5": 0}
    rows = []
    for scene_id, answer, eval_result, subgoal, kind in ROUND3_CASES:
        sc = get_scene(scene_id)
        old_ctx = build_old_context(scene_id, answer, eval_result, subgoal)
        new_ctx = build_new_context(scene_id, answer, eval_result, subgoal)
        old_msg = user_message(old_ctx)
        new_msg = user_message(new_ctx)

        # v4 旧：1 次
        raw_v4, dt_v4 = call(v4_system(sc["coach_context"]) + "\n" + COACH_DECISION_FORMAT, old_msg)
        timings["v4"] += dt_v4
        counts["v4"] += 1

        # v5 新：3 次
        v5_runs = []
        for i in range(3):
            raw, dt = call(v5_system(sc["coach_context"], sc.get("coach_persona", "")) + "\n" + COACH_DECISION_FORMAT, new_msg)
            v5_runs.append(parse(raw))
            timings["v5"] += dt
            counts["v5"] += 1

        row = {
            "scene": sc["label"],
            "scene_id": scene_id,
            "answer": answer,
            "kind": kind,
            "v4": parse(raw_v4),
            "v5": v5_runs,
        }
        rows.append(row)
        print(f"[{sc['label']}] {kind} done", file=sys.stderr)

    # 控制台：逐条对照
    print("\n=== 逐条对照（v5 三次并列）===")
    for r in rows:
        print(f"\n[{r['scene']}]（{r['kind']}）\n  用户：{r['answer']}")
        v4 = r["v4"]
        print(f"  v4旧：    [{v4.get('action_type')}] {v4.get('reply_text')}")
        for i, v in enumerate(r["v5"], 1):
            print(f"  v5新 #{i}： [{v.get('action_type')}] {v.get('reply_text')}")

    # 控制台：原始回复（可追溯）
    print("\n=== 原始回复 JSON（可追溯）===")
    for r in rows:
        print(f"\n[{r['scene']}] 用户：{r['answer']}")
        print(f"  v4旧: {r['v4'].get('raw')}")
        for i, v in enumerate(r["v5"], 1):
            print(f"  v5新#{i}: {v.get('raw')}")

    print("\n=== 时延汇总（未含 TTS）===")
    for name in ("v4", "v5"):
        print(f"{name}：{timings[name]:.1f}s / {counts[name]} 次调用")

    # 结果落盘（data/ 已 gitignore，便于后续整理证据）
    out_path = Path(__file__).resolve().parents[1] / "data" / "round3_v5_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "prompt_version": COACH_SYSTEM_PROMPT_VERSION,
        "cases": rows,
        "timings": timings,
        "counts": counts,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\n结果已保存：{out_path}")


if __name__ == "__main__":
    main()
