"""P0 第 2 项：真实服务复跑「求助 / 回顾 / 复练」（PRD 15.1 第 2 条）。

对运行中的真实后端（PROVIDER_MODE=real）走一遍：
  1. 四类求助（repeat / explain / organize / translate）→ 记录真实模型回复
  2. 一个完整会话 → 结束 → 记录回顾（review）
  3. 把回顾里的改进项加入待复练队列 → 列表 → 到期检查 → 复测（未到期应被拒，AC07-08）

不打印密钥。用法：
  cd backend && python scripts/replay_help_review.py --dry-run
  cd backend && python scripts/replay_help_review.py
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = os.getenv("BACKEND_BASE", "http://127.0.0.1:8000")
INVITE = {"X-Invite-Code": "test-a"}


def post(path, **kw):
    r = httpx.post(BASE + path, headers=INVITE, timeout=60, **kw)
    return r


def get(path, **kw):
    r = httpx.get(BASE + path, headers=INVITE, timeout=30, **kw)
    return r


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
    return {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        print("[干跑] 将用 X-Invite-Code: test-a 对真实后端走：求助 4 类 → 完整会话 → 回顾 → 队列加/列/复测")
        return

    print("=== 1) 四类求助（真实模型）===")
    s = post("/api/v1/sessions", json={"level": "basic", "difficulty": "standard", "scene_id": "hotel_checkin"})
    assert s.status_code == 200, s.text
    sid = s.json()["session_id"]
    for help_type, level in [("repeat", 0), ("explain", 0), ("organize", 1), ("organize", 3), ("translate", 0)]:
        r = post(f"/api/v1/sessions/{sid}/help", json={"help_type": help_type, "help_level": level})
        body = r.json()
        print(f"  [{help_type} L{level}] HTTP {r.status_code}: {body.get('reply_text', body)}")
    post(f"/api/v1/sessions/{sid}/end", json={"reason": "user_ended"})

    print("\n=== 2) 完整会话 → 回顾 ===")
    s2 = post("/api/v1/sessions", json={"level": "basic", "difficulty": "standard", "scene_id": "job_interview"})
    assert s2.status_code == 200, s2.text
    sid2 = s2.json()["session_id"]
    turns = [
        "just working",  # 期望 incomplete → 生成改进项
        "I have three years of experience in software development.",
        "I worked on a project with my team and we delivered it on time.",
    ]
    for text in turns:
        t0 = time.perf_counter()
        r = post(f"/api/v1/sessions/{sid2}/turns/text", json={"text": text})
        done = sse_done(r.text)
        dt = time.perf_counter() - t0
        print(f"  用户「{text}」→ eval={done.get('eval_result')} action={done.get('action_type')} status={done.get('status')} ({dt:.1f}s)")
        if done.get("status") == "ENDED":
            break
    # 结束并取回顾
    post(f"/api/v1/sessions/{sid2}/end", json={"reason": "user_ended"})
    detail = get(f"/api/v1/sessions/{sid2}").json()
    review = detail.get("review") or {}
    print(f"  回顾 goal_status={review.get('goal_status')} 改进项数={len(review.get('improvements', []))}")
    for imp in review.get("improvements", []):
        print(f"    改进项 turn_id={imp.get('turn_id')} 原句「{imp.get('original')}」")

    print("\n=== 3) 待复练队列（加 / 列 / 到期 / 复测）===")
    improvements = review.get("improvements", [])
    if not improvements:
        print("  无改进项，跳过队列（正常：本会话可能全部 independent）")
        return
    imp = improvements[0]
    r = post("/api/v1/review-queue", json={"source_session_id": sid2, "source_turn_id": imp["turn_id"]})
    print(f"  加入队列：HTTP {r.status_code} {r.json()}")
    items = get("/api/v1/review-queue").json()
    print(f"  队列列表：{len(items)} 条")
    due = get("/api/v1/review-queue/due").json()
    print(f"  到期项：{due if due else '无（未到期，符合 AC07-08）'}")
    if items:
        item_id = items[0]["id"]
        rt = post(f"/api/v1/review-queue/{item_id}/retest", json={"answer": "I have three years of experience.", "hint_used": False})
        print(f"  复测：HTTP {rt.status_code} {rt.json()}")


if __name__ == "__main__":
    main()
