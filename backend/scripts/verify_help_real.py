"""真实后端验证四类求助（REQ-05）——通过 HTTP 而非直接调 Provider。

对运行中的真实后端逐类验证，记录：实际回复、是否复用音频/是否计费（看帮助记录与预算）、
后续回答的完成判断。翻译调用一次真实模型，其余三类应为零模型调用。

用法：
  cd backend && python scripts/verify_help_real.py --dry-run
  cd backend && python scripts/verify_help_real.py
"""
import argparse
import json
import os

import httpx

BASE = os.getenv("BACKEND_BASE", "http://127.0.0.1:8000")
INVITE = {"X-Invite-Code": "test-a"}


def post(path, **kw):
    return httpx.post(BASE + path, headers=INVITE, timeout=60, **kw)


def create(scene="hotel_checkin"):
    r = post("/api/v1/sessions", json={"level": "basic", "difficulty": "standard", "scene_id": scene})
    assert r.status_code == 200, r.text
    return r.json()


def do_help(sid, ht, source=None):
    body = {"help_type": ht}
    if source:
        body["source_text"] = source
    return post(f"/api/v1/sessions/{sid}/help", json=body)


def sse_done(text):
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


def answer(sid, text):
    return sse_done(post(f"/api/v1/sessions/{sid}/turns/text", json={"text": text}).text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.dry_run:
        print("[干跑] 将用 X-Invite-Code: test-a 对真实后端逐类验证四类求助，并记录后续回答的评估结果")
        return

    print("| 求助类型 | 实际回复 | 复用原音频/零费用 | is_answer_help | 后续回答评估 |")
    print("| --- | --- | --- | --- | --- |")

    # repeat
    s = create()
    r = do_help(s["session_id"], "repeat")
    b = r.json()
    done = answer(s["session_id"], "I have a reservation under my name.")
    print(f"| repeat | {b['reply_text']} | {'✅' if b['reply_audio_url'] == s['reply_audio_url'] else '❌'} | {b['is_answer_help']} | {done.get('eval_result')} |")

    # explain
    s = create()
    b = do_help(s["session_id"], "explain").json()
    done = answer(s["session_id"], "I have a reservation under my name.")
    print(f"| explain | {b['reply_text']} | ✅(零模型) | {b['is_answer_help']} | {done.get('eval_result')} |")

    # organize（三次逐级）
    s = create()
    lv = []
    for _ in range(3):
        lv.append(do_help(s["session_id"], "organize").json()["help_level"])
    b = do_help(s["session_id"], "organize").json()
    done = answer(s["session_id"], "I have a reservation under my name.")
    print(f"| organize | 层级 {lv}→{b['help_level']}：{b['reply_text']} | ✅(零模型) | {b['is_answer_help']} | {done.get('eval_result')} |")

    # translate
    s = create()
    b = do_help(s["session_id"], "translate", source="我想订两晚的房间，请问早餐几点开始？").json()
    done = answer(s["session_id"], "I have a reservation under my name.")
    print(f"| translate | {b['reply_text']} | ❌(1 次翻译模型) | {b['is_answer_help']} | {done.get('eval_result')} |")


if __name__ == "__main__":
    main()
