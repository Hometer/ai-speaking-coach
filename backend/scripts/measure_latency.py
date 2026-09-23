"""真实时延 / 失败率 / 费用采集（P0 继承基线收口第 5 项，PRD 15.1）。

对真实百炼服务直接测量一个「普通回合」的模型调用链：
  1. 独立评估（qwen-plus）
  2. 教练决策（qwen-plus）
  3. 语音合成 TTS（cosyvoice-v2，用教练回复文本）

每个回合记录三段的墙钟时延、LLM 的 input/output tokens、TTS 的字符数，
最后汇总：各段 P50/P95、失败率、单回合估算成本。

参考单价（元，以百炼控制台实际单价为准）：
  qwen-plus 输入 0.0008 / 千 tokens，输出 0.002 / 千 tokens；
  cosyvoice-v2 TTS 按 2.0 / 千字符估算。

用法：
  cd backend && python scripts/measure_latency.py --dry-run     # 只打印计划，不调用
  cd backend && python scripts/measure_latency.py --turns 50    # 真实运行（需先确认费用）
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.services.prompts.coach import COACH_DECISION_FORMAT, EVAL_SYSTEM_PROMPT, coach_system_prompt
from app.services.prompts.scenarios import get_scene

# 参考单价（元）
PRICE_LLM_IN = 0.0008
PRICE_LLM_OUT = 0.002
PRICE_TTS_PER_1K_CHAR = 2.0

# 每场景一条代表性回答（用于构造真实上下文，不泄露示例材料）
SAMPLE_ANSWERS = {
    "job_interview": "I have three years of experience in software development.",
    "new_colleague": "I am a designer on the product team.",
    "meeting_opinion": "I think we should launch the smaller version first.",
    "hotel_checkin": "I have a reservation under my name.",
    "restaurant_order": "I would like the grilled chicken, please.",
    "new_friend": "I am a teacher and I like hiking.",
    "hobby": "I like painting in my free time.",
}
SCENE_IDS = list(SAMPLE_ANSWERS.keys())


def build_contexts(scene_id: str, answer: str) -> tuple[dict, dict]:
    sc = get_scene(scene_id)
    coach_ctx = {
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
        "eval_result": "independent",
        "subgoal": sc["subgoals"][0],
        "history": [],
        "rechecked": False,
        "memories": [],
    }
    eval_ctx = {
        "question": sc["openings"]["basic"],
        "answer": answer,
        "hint_used": False,
        "input_type": "text",
        "goal": sc["goal_text"],
        "subgoal": sc["subgoals"][0],
        "acceptable_points": sc.get("acceptable_points", []),
    }
    return coach_ctx, eval_ctx


def call_llm(system: str, user: str) -> tuple[str, dict, float, str | None]:
    """返回 (内容, usage, 秒, 错误)。"""
    import dashscope

    dashscope.api_key = settings.dashscope_api_key
    t0 = time.perf_counter()
    try:
        resp = dashscope.Generation.call(
            model=settings.model_llm,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            result_format="message",
        )
    except Exception as exc:  # noqa: BLE001
        return "", {}, time.perf_counter() - t0, f"{type(exc).__name__}: {str(exc)[:80]}"
    dt = time.perf_counter() - t0
    if getattr(resp, "status_code", None) != 200:
        return "", {}, dt, f"status {getattr(resp, 'status_code', None)} {getattr(resp, 'message', '')[:80]}"
    usage = {}
    u = getattr(resp, "usage", None)
    if u is not None:
        usage = {
            "input_tokens": getattr(u, "input_tokens", 0) or 0,
            "output_tokens": getattr(u, "output_tokens", 0) or 0,
        }
    return resp.output.choices[0].message.content.strip(), usage, dt, None


def call_tts(text: str) -> tuple[float, str | None]:
    """返回 (秒, 错误)。"""
    import dashscope
    from dashscope.audio.tts_v2 import SpeechSynthesizer

    dashscope.api_key = settings.dashscope_api_key
    t0 = time.perf_counter()
    try:
        synthesizer = SpeechSynthesizer(model=settings.model_tts, voice=settings.tts_voice, language_hints=["en"])
        audio = synthesizer.call(text)
    except Exception as exc:  # noqa: BLE001
        return time.perf_counter() - t0, f"{type(exc).__name__}: {str(exc)[:80]}"
    dt = time.perf_counter() - t0
    if not audio:
        return dt, "TTS 返回空音频"
    return dt, None


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summarize(name: str, values: list[float]) -> str:
    if not values:
        return f"{name}: 无样本"
    return (
        f"{name}: n={len(values)} P50={percentile(values, 0.5):.2f}s "
        f"P95={percentile(values, 0.95):.2f}s 均值={statistics.mean(values):.2f}s"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=50)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    n = args.turns
    print(f"=== 真实时延/失败率/费用采集（{n} 回合，每回合 = 评估 + 教练 + TTS，共 {n * 3} 次模型调用）===")
    print(f"模式：PROVIDER_MODE={settings.provider_mode} 模型：{settings.model_llm} / {settings.model_tts}（{settings.tts_voice}）")

    if args.dry_run:
        sc = get_scene("hobby")
        coach_ctx, eval_ctx = build_contexts("hobby", SAMPLE_ANSWERS["hobby"])
        print("\n[干跑] 只打印样本上下文，不调用模型：")
        print("coach_ctx:", json.dumps(coach_ctx, ensure_ascii=False))
        print("eval_ctx:", json.dumps(eval_ctx, ensure_ascii=False))
        print(f"\n[干跑] 将真实调用 {n} 次评估 + {n} 次教练 + {n} 次 TTS = {n * 3} 次")
        return

    coach_lat, eval_lat, tts_lat, turn_lat = [], [], [], []
    failures = 0
    total_llm_in = 0
    total_llm_out = 0
    total_tts_chars = 0

    for i in range(n):
        scene_id = SCENE_IDS[i % len(SCENE_IDS)]
        answer = SAMPLE_ANSWERS[scene_id]
        coach_ctx, eval_ctx = build_contexts(scene_id, answer)

        # 1. 评估
        eval_raw, eval_usage, e_dt, e_err = call_llm(
            EVAL_SYSTEM_PROMPT, json.dumps(eval_ctx, ensure_ascii=False)
        )
        # 2. 教练
        coach_raw, coach_usage, c_dt, c_err = call_llm(
            coach_system_prompt(coach_ctx["scene_context"], coach_ctx["coach_persona"]) + "\n" + COACH_DECISION_FORMAT,
            "Session context as JSON:\n" + json.dumps(coach_ctx, ensure_ascii=False)
            + "\nChoose the next teaching action. The follow-up must be based on what the user actually said.",
        )
        # 3. TTS（用教练回复文本）
        reply_text = ""
        if not c_err:
            try:
                reply_text = str(json.loads(coach_raw).get("reply_text", "")).strip()
            except Exception:
                reply_text = coach_raw
        if not reply_text:
            reply_text = "Let's continue."

        t_dt, t_err = call_tts(reply_text)

        if e_err or c_err or t_err:
            failures += 1
            print(f"[回合 {i + 1} 失败] eval={e_err} coach={c_err} tts={t_err}", file=sys.stderr)

        eval_lat.append(e_dt)
        coach_lat.append(c_dt)
        tts_lat.append(t_dt)
        turn_lat.append(e_dt + c_dt + t_dt)
        total_llm_in += eval_usage.get("input_tokens", 0) + coach_usage.get("input_tokens", 0)
        total_llm_out += eval_usage.get("output_tokens", 0) + coach_usage.get("output_tokens", 0)
        total_tts_chars += len(reply_text)

    print("\n=== 时延（未含网络/HTTP 开销，未含真实 ASR）===")
    print(summarize("评估 LLM", eval_lat))
    print(summarize("教练 LLM", coach_lat))
    print(summarize("TTS", tts_lat))
    print(summarize("单回合合计(估)", turn_lat))

    print(f"\n=== 失败率 ===")
    print(f"{failures}/{n} 回合失败（任一模型调用失败即计）")

    print("\n=== 费用估算（参考单价，以百炼控制台实际为准）===")
    llm_cost = (total_llm_in / 1000) * PRICE_LLM_IN + (total_llm_out / 1000) * PRICE_LLM_OUT
    tts_cost = (total_tts_chars / 1000) * PRICE_TTS_PER_1K_CHAR
    print(f"LLM：in={total_llm_in} tokens / out={total_llm_out} tokens → 约 ¥{llm_cost:.3f}")
    print(f"TTS：{total_tts_chars} 字符 → 约 ¥{tts_cost:.3f}")
    print(f"合计：约 ¥{llm_cost + tts_cost:.3f}（{n} 回合），单回合约 ¥{(llm_cost + tts_cost) / n:.3f}")

    out = Path(__file__).resolve().parents[1] / "data" / "latency_measurement.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "turns": n,
        "failures": failures,
        "coach_latency": coach_lat,
        "eval_latency": eval_lat,
        "tts_latency": tts_lat,
        "turn_latency": turn_lat,
        "llm_input_tokens": total_llm_in,
        "llm_output_tokens": total_llm_out,
        "tts_chars": total_tts_chars,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\n原始数据已保存：{out}")


if __name__ == "__main__":
    main()
