"""七场景真实冒烟：创建会话 + 发一条代表性回答，记录真实评估/教练动作/回复，验证场景化。

- 通过 HTTP 走真实后端（PROVIDER_MODE=real），覆盖完整链路（开场 TTS + 回合评估 + 教练 + TTS）。
- 只发一条文字回合，重点看：教练追问是否贴合场景、是否无英文面试残留。
- 不打印密钥。真实 ASR / 讯飞评测不参与（需真实录音，待手机真机）。

用法：
  cd backend && python scripts/smoke_scenes_real.py --dry-run
  cd backend && python scripts/smoke_scenes_real.py
  cd backend && python scripts/smoke_scenes_real.py --base http://127.0.0.1:8001
"""
import argparse
import json
import os
import sys
import time

import httpx

DEFAULT_BASE = os.getenv("BACKEND_BASE", "http://127.0.0.1:8000")
INVITE = {"X-Invite-Code": "test-a"}

# 七场景：scene_id -> 代表性回答（不使用示例材料里的 Li/Alex，避免照抄）
SCENES = [
    ("job_interview", "英文面试", "I have three years of experience in software development."),
    ("hotel_checkin", "酒店入住", "I have a reservation under my name."),
    ("meeting_opinion", "会议中表达意见", "I think we should launch the smaller version first."),
    ("new_colleague", "认识新同事", "I am a designer on the product team."),
    ("restaurant_order", "餐厅点餐", "I would like the grilled chicken, please."),
    ("new_friend", "认识新朋友", "I am a teacher and I like hiking."),
    ("hobby", "聊兴趣爱好", "I like painting in my free time."),
]


def post(base: str, path: str, **kw):
    return httpx.post(base + path, headers=INVITE, timeout=60, **kw)


def sse_done(text: str) -> dict:
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event, data = None, None
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if event == "done" and data is not None:
            return data
        if event == "error" and data is not None:
            raise RuntimeError(f"回合失败：{data.get('error', {}).get('code', 'UNKNOWN')}")
    raise RuntimeError("回合未收到 done/error 终止事件")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--base", default=DEFAULT_BASE)
    args = ap.parse_args()
    base = args.base.rstrip("/")

    print(f"=== 七场景真实冒烟（BASE={base}）===")
    if args.dry_run:
        print("[干跑] 将对七个场景各创建会话并发送一条文字回合，记录评估/教练动作/回复。")
        return

    rows = []
    for scene_id, name, answer in SCENES:
        t0 = time.perf_counter()
        r = post(base, "/api/v1/sessions", json={"level": "basic", "difficulty": "standard", "scene_id": scene_id})
        if r.status_code != 200:
            print(f"[{name}] 创建失败 HTTP {r.status_code}: {r.text[:120]}")
            rows.append((name, "创建失败", "", "", r.text[:80]))
            continue
        sid = r.json()["session_id"]
        opening = r.json().get("reply_text", "")
        tr = post(base, f"/api/v1/sessions/{sid}/turns/text", json={"text": answer})
        try:
            done = sse_done(tr.text)
        except RuntimeError as exc:
            print(f"[{name}] {exc}")
            rows.append((name, "回合失败", "", "", ""))
            post(base, f"/api/v1/sessions/{sid}/end", json={"reason": "user_ended"})
            continue
        dt = time.perf_counter() - t0
        reply = done.get("reply_text", "")
        print(f"\n[{name}] 开场「{opening[:60]}」")
        print(f"  用户「{answer}」")
        print(f"  eval={done.get('eval_result')} action={done.get('action_type')} ({dt:.1f}s)")
        print(f"  教练「{reply[:160]}」")
        rows.append((name, done.get("eval_result", ""), done.get("action_type", ""), reply, ""))
        post(base, f"/api/v1/sessions/{sid}/end", json={"reason": "user_ended"})

    print("\n=== 汇总表 ===")
    print("| 场景 | 评估 | 动作 | 教练回复 |")
    print("| --- | --- | --- | --- |")
    for name, ev, act, reply, _ in rows:
        print(f"| {name} | {ev} | {act} | {reply[:80]} |")


if __name__ == "__main__":
    main()
