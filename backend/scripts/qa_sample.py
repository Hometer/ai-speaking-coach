"""教学质检抽样（P0 第 6 项，PRD 13.3）。

对真实百炼跑「独立评估 + 教练决策」各一次（不跑 TTS），生成供产品经理判定的样本：
  误纠错率 ≤5%（≥30 条）、错误宣告独立完成 ≤5%（≥30 条）、无关追问率 ≤10%（≥30 条）。

每个样本记录：场景、类型（正确/极短/语法误/跑题/部分）、用户原话、评估结果、
教练动作与回复，便于逐条判定。不打印密钥。

用法：
  cd backend && python scripts/qa_sample.py --dry-run     # 只打印样本清单
  cd backend && python scripts/qa_sample.py               # 真实运行（约 70 次 LLM 调用）
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.coach import get_coach_provider
from app.services.evaluator import get_evaluator_provider
from app.services.prompts.scenarios import get_scene

# (scene_id, 类型, 用户回答)
CASES = [
    ("job_interview", "正确", "I have three years of experience in software development."),
    ("job_interview", "极短/语法误", "uh... three year"),
    ("job_interview", "跑题", "I am very good, I like food and music."),
    ("job_interview", "正确", "I worked on a project with my team, and we delivered it on time."),
    ("job_interview", "正确", "I have five years of experience in sales."),
    ("new_colleague", "正确", "I am a designer on the product team."),
    ("new_colleague", "极短", "Hi."),
    ("new_colleague", "部分", "I am from Beijing."),
    ("new_colleague", "正确+反问", "I just joined the sales team. What about you?"),
    ("new_colleague", "部分", "I work with data."),
    ("meeting_opinion", "正确", "I think we should launch the smaller version first."),
    ("meeting_opinion", "极短", "Maybe."),
    ("meeting_opinion", "跑题", "I think the weather is nice today."),
    ("meeting_opinion", "正确", "We should move the deadline because the team needs more time."),
    ("meeting_opinion", "部分", "I agree with the plan."),
    ("hotel_checkin", "正确", "I have a reservation under my name."),
    ("hotel_checkin", "极短", "Yes."),
    ("hotel_checkin", "跑题", "I want to buy a car."),
    ("hotel_checkin", "正确+问服务", "I have a booking for two nights. What time is breakfast?"),
    ("hotel_checkin", "部分", "My name is John."),
    ("restaurant_order", "正确", "I would like the grilled chicken, please."),
    ("restaurant_order", "极短", "Water."),
    ("restaurant_order", "跑题", "I like basketball."),
    ("restaurant_order", "正确", "I'll have the pasta, no onions please."),
    ("restaurant_order", "部分", "Something to eat."),
    ("new_friend", "正确", "I am a teacher and I like hiking."),
    ("new_friend", "极短", "Hi."),
    ("new_friend", "跑题", "I am hungry."),
    ("new_friend", "正确+反问", "I work in finance. And you?"),
    ("new_friend", "部分", "My name is Anna."),
    ("hobby", "正确", "I like painting because it helps me relax."),
    ("hobby", "极短", "Yes."),
    ("hobby", "部分/跑题", "I don't like anything."),
    ("hobby", "正确", "I enjoy swimming every Saturday morning."),
    ("hobby", "极短", "Painting."),
]


def eval_context(scene_id: str, answer: str) -> dict:
    sc = get_scene(scene_id)
    return {
        "question": sc["openings"]["basic"],
        "answer": answer,
        "hint_used": False,
        "input_type": "text",
        "goal": sc["goal_text"],
        "subgoal": sc["subgoals"][0],
        "acceptable_points": sc.get("acceptable_points", []),
    }


def coach_context(scene_id: str, answer: str, eval_result: str) -> dict:
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
        "subgoal": sc["subgoals"][0],
        "history": [],
        "rechecked": False,
        "memories": [],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print(f"=== 教学质检抽样：{len(CASES)} 条，每条 = 评估 + 教练 各 1 次真实调用 ===")
    if args.dry_run:
        for scene_id, kind, answer in CASES:
            sc = get_scene(scene_id)
            print(f"[{sc['label']}] {kind}：{answer}")
        return

    evaluator = get_evaluator_provider()
    coach = get_coach_provider()
    rows = []
    for scene_id, kind, answer in CASES:
        sc = get_scene(scene_id)
        try:
            ev = evaluator.evaluate(eval_context(scene_id, answer))
        except Exception as exc:  # noqa: BLE001
            ev = None
            print(f"[{sc['label']}] 评估失败：{type(exc).__name__} {str(exc)[:80]}", file=sys.stderr)
        eval_result = ev.result if ev else "ERROR"
        try:
            dc = coach.decide(coach_context(scene_id, answer, eval_result))
        except Exception as exc:  # noqa: BLE001
            dc = None
            print(f"[{sc['label']}] 教练失败：{type(exc).__name__} {str(exc)[:80]}", file=sys.stderr)
        rows.append({
            "scene": sc["label"],
            "kind": kind,
            "answer": answer,
            "eval_result": eval_result,
            "action_type": dc.action_type if dc else "ERROR",
            "reply_text": dc.reply_text if dc else "ERROR",
        })

    print("\n=== 质检样本表（供逐条判定）===\n")
    print("| # | 场景 | 类型 | 用户说的 | 评估结果 | 教练动作 | 教练回复 |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for i, r in enumerate(rows, 1):
        print(f"| {i} | {r['scene']} | {r['kind']} | {r['answer']} | {r['eval_result']} | {r['action_type']} | {r['reply_text']} |")

    print("\n=== 判定辅助（供你勾选，无需回填，我按你的结论统计）===")
    print("- 误纠错：教练是否把本来说得对/能懂的话判成错误并纠正？")
    print("- 错误宣告独立：评估结果标 independent，但用户其实没答全/答太短？")
    print("- 无关追问：教练追问与场景或用户刚说的无关？")

    out = Path(__file__).resolve().parents[1] / "data" / "qa_sample.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    print(f"\n原始数据已保存：{out}")


if __name__ == "__main__":
    main()
