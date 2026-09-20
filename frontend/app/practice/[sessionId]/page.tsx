"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { disputeReview, endSession, getInviteCode, getSession, requestHint, reviseTurn, skipPractice, submitTurn } from "@/lib/api";
import type { TurnDone } from "@/lib/types";
import AudioPlayer from "@/components/AudioPlayer";

interface Msg {
  who: "user" | "coach";
  text: string;
  audioUrl?: string;
  turnId?: string;
}

const STATUS_CN: Record<string, string> = {
  WAITING_INPUT: "等待回答",
  RECHECK: "复测中",
  PRACTICE: "跟读中",
  ENDED: "已结束",
  PAUSED: "已暂停",
};

export default function PracticePage() {
  const params = useParams<{ sessionId: string }>();
  const sessionId = params.sessionId;
  const router = useRouter();

  const [goal, setGoal] = useState("");
  const [status, setStatus] = useState("");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [practiceSentence, setPracticeSentence] = useState("");
  const [practiceDemo, setPracticeDemo] = useState("");
  const [practiceResult, setPracticeResult] = useState("");
  const [recording, setRecording] = useState(false);
  const [recordMode, setRecordMode] = useState<"answer" | "practice" | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [review, setReview] = useState<Record<string, unknown> | null>(null);

  const recRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const cancelRef = useRef(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    getSession(sessionId)
      .then((d) => {
        setGoal(d.goal_text);
        setStatus(d.status);
        const msgs: Msg[] = [];
        for (const t of d.turns) {
          if (t.input_type === "coach") {
            msgs.push({ who: "coach", text: t.reply_text, audioUrl: t.reply_audio_url });
          } else {
            msgs.push({ who: "user", text: t.user_text || t.transcript || "（语音回答）" });
          }
        }
        setMessages(msgs);
      })
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [sessionId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  function addMsg(who: Msg["who"], text: string, audioUrl?: string, turnId?: string) {
    setMessages((m) => [...m, { who, text, audioUrl, turnId }]);
  }

  async function handleTurn(input: { text?: string; audio?: Blob }, typedText?: string) {
    setBusy(true);
    setErr("");
    try {
      const done: TurnDone = await submitTurn(sessionId, input);
      if (typedText) addMsg("user", typedText, undefined, done.turn_id);
      else if (done.transcript) addMsg("user", done.transcript, undefined, done.turn_id);
      else addMsg("user", "（语音回答）", undefined, done.turn_id);
      addMsg("coach", done.reply_text, done.reply_audio_url);
      setStatus(done.status);
      if (done.action_type === "suggest_practice" && done.practice_sentence) {
        setPracticeSentence(done.practice_sentence);
        setPracticeDemo(done.reply_audio_url);
        setPracticeResult("");
      } else {
        setPracticeSentence("");
        setPracticeDemo("");
      }
      if (done.status === "ENDED") {
        setErr("目标已达成，可点「结束练习」查看回顾。");
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function sendText() {
    const t = text.trim();
    if (!t) return;
    setText("");
    handleTurn({ text: t }, t);
  }

  function clearTimer() {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }

  function timerText() {
    if (!recording) return "";
    if (elapsed >= 50) return `还剩 ${60 - elapsed} 秒`;
    return `已录 ${elapsed} 秒`;
  }

  async function toggleRecord(mode: "answer" | "practice") {
    if (recRef.current && recRef.current.state === "recording") {
      cancelRef.current = false;
      recRef.current.stop();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      chunksRef.current = [];
      cancelRef.current = false;
      setElapsed(0);
      const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : "";
      const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      recRef.current = rec;
      rec.ondataavailable = (e) => {
        if (e.data.size) chunksRef.current.push(e.data);
      };
      rec.onstop = () => {
        clearTimer();
        stream.getTracks().forEach((t) => t.stop());
        setRecording(false);
        setRecordMode(null);
        setElapsed(0);
        if (cancelRef.current) {
          cancelRef.current = false;
          return;
        }
        const blob = new Blob(chunksRef.current, { type: rec.mimeType || "audio/webm" });
        if (mode === "answer") handleTurn({ audio: blob });
        else submitPractice(blob);
      };
      rec.start();
      setRecording(true);
      setRecordMode(mode);
      timerRef.current = setInterval(() => {
        setElapsed((prev) => {
          const next = prev + 1;
          if (next >= 60 && recRef.current && recRef.current.state === "recording") {
            cancelRef.current = false;
            recRef.current.stop();
          }
          return next;
        });
      }, 1000);
    } catch {
      setErr("无法获取麦克风，可用文字输入。");
    }
  }

  function cancelRecord() {
    if (recRef.current && recRef.current.state === "recording") {
      cancelRef.current = true;
      recRef.current.stop();
    }
  }

  async function submitPractice(blob: Blob) {
    setBusy(true);
    setErr("");
    try {
      const fd = new FormData();
      fd.append("audio", blob, "practice");
      fd.append("sentence", practiceSentence);
      const res = await fetch(`/api/v1/sessions/${sessionId}/practice`, {
        method: "POST",
        headers: { "X-Invite-Code": getInviteCode() },
        body: fd,
      });
      const data = await res.json();
      if (!res.ok) {
        setErr(data?.error?.message ?? `HTTP ${res.status}`);
        return;
      }
      const score = typeof data.eval?.score === "number" ? data.eval.score : null;
      const isMock = !!data.eval?.mock;
      setPracticeResult(score === null ? "本次无法评估" : `评测得分 ${score}${isMock ? "（示例）" : "（实测）"}`);
      addMsg("coach", data.recheck_reply_text, data.recheck_audio_url);
      setStatus(data.status);
      setPracticeSentence("");
      setPracticeDemo("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleSkipPractice() {
    setBusy(true);
    setErr("");
    try {
      const r = await skipPractice(sessionId);
      addMsg("coach", r.reply_text, r.reply_audio_url);
      setStatus(r.status);
      setPracticeSentence("");
      setPracticeDemo("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleHint() {
    setBusy(true);
    setErr("");
    try {
      const r = await requestHint(sessionId);
      addMsg("coach", r.reply_text, r.reply_audio_url);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleRevise(turnId: string) {
    const newText = window.prompt("修改转写内容：", "");
    if (!newText || !newText.trim()) return;
    try {
      await reviseTurn(sessionId, turnId, newText.trim());
      setErr("已提交修订，该回合评分已作废。");
      const d = await getSession(sessionId);
      const msgs: Msg[] = [];
      for (const t of d.turns) {
        if (t.input_type === "coach") msgs.push({ who: "coach", text: t.reply_text });
        else if (t.input_type === "practice") msgs.push({ who: "user", text: `（跟读：${t.practice_sentence}）` });
        else {
          const u = t.revised_text || t.user_text || t.transcript;
          if (u) msgs.push({ who: "user", text: u, turnId: t.turn_id });
          if (t.reply_text) msgs.push({ who: "coach", text: t.reply_text });
        }
      }
      setMessages(msgs);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleDispute(turnId: string) {
    try {
      await disputeReview(sessionId, turnId);
      setErr("已标记'有误'，该建议不再用于记忆。");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function end() {
    setBusy(true);
    setErr("");
    try {
      const r = await endSession(sessionId);
      setStatus("ENDED");
      setReview(r.review);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const improvements = Array.isArray(review?.improvements) ? (review.improvements as Record<string, unknown>[]) : [];

  return (
    <main className="min-h-full flex flex-col items-center px-4 py-6">
      <div className="w-full max-w-2xl flex flex-col" style={{ minHeight: "calc(100vh - 48px)" }}>
        <div className="flex items-center justify-between mb-3">
          <button
            onClick={() => router.push("/")}
            className="text-sm px-3 py-1.5 rounded-lg border"
            style={{ borderColor: "var(--line)", color: "var(--muted)" }}
          >
            ← 返回
          </button>
          <span
            className="text-xs px-2.5 py-1 rounded-full"
            style={{ background: "var(--brand-soft)", color: "var(--brand-strong)" }}
          >
            {STATUS_CN[status] || status || "加载中"}
          </span>
        </div>

        <div
          className="rounded-xl border px-4 py-3 mb-4"
          style={{ background: "var(--brand-soft)", borderColor: "var(--brand-soft)" }}
        >
          <div className="text-xs mb-1" style={{ color: "var(--muted)" }}>
            本次目标
          </div>
          <div className="text-sm font-medium">{goal}</div>
        </div>

        <div className="flex-1 space-y-3 mb-4">
          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.who === "user" ? "justify-end" : "justify-start"}`}>
              <div
                className="max-w-[80%] rounded-2xl px-4 py-2.5 text-sm leading-6 whitespace-pre-wrap"
                style={{
                  background: m.who === "user" ? "var(--user-bubble)" : "var(--ai-bubble)",
                  border: m.who === "coach" ? "1px solid var(--line)" : "none",
                }}
              >
                <div className="text-xs mb-1" style={{ color: "var(--muted)" }}>
                  {m.who === "user" ? "你说的" : "AI 教练"}
                </div>
                {m.text}
                {m.who === "user" && m.turnId ? (
                  <button onClick={() => handleRevise(m.turnId!)} className="text-xs mt-1 block" style={{ color: "var(--muted)" }}>修订转写</button>
                ) : null}
                {m.audioUrl ? <AudioPlayer src={m.audioUrl} /> : null}
              </div>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        {practiceSentence ? (
          <div
            className="rounded-xl border p-4 mb-4"
            style={{ background: "var(--ai-bubble)", borderColor: "var(--brand)" }}
          >
            <div className="text-sm font-medium mb-1">跟读练习</div>
            <div className="text-base mb-2">{practiceSentence}</div>
            {practiceDemo ? <AudioPlayer src={practiceDemo} /> : null}
            <div className="flex items-center gap-3">
              <button
                onClick={() => toggleRecord("practice")}
                disabled={busy}
                className="rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                style={{ background: recording && recordMode === "practice" ? "var(--brand-strong)" : "var(--brand)" }}
              >
                {recording && recordMode === "practice" ? "停止" : "录音跟读"}
              </button>
              <button onClick={handleSkipPractice} disabled={busy} className="text-xs px-2 py-1 rounded-lg border" style={{ borderColor: "var(--line)", color: "var(--muted)" }}>跳过</button>
              {recording && recordMode === "practice" ? (
                <>
                  <span className="text-xs" style={{ color: elapsed >= 50 ? "var(--danger, #dc2626)" : "var(--muted)" }}>{timerText()}</span>
                  <button onClick={cancelRecord} className="text-xs px-2 py-1 rounded-lg border" style={{ borderColor: "var(--line)", color: "var(--danger, #dc2626)" }}>取消</button>
                </>
              ) : null}
              <span className="text-xs" style={{ color: "var(--muted)" }}>
                {practiceResult}
              </span>
            </div>
          </div>
        ) : null}

        {!practiceSentence && practiceResult ? (
          <div className="text-xs mb-3" style={{ color: "var(--muted)" }}>
            上次跟读：{practiceResult}
          </div>
        ) : null}

        {review ? (
          <div className="rounded-xl border p-4 mb-4" style={{ background: "var(--ai-bubble)", borderColor: "var(--line)" }}>
            <div className="text-sm font-medium mb-2">练习回顾</div>
            {improvements.length ? (
              <div className="space-y-2 text-sm">
                {improvements.map((imp, i) => (
                  <div key={i} className="border-l-2 pl-3" style={{ borderColor: "var(--brand)" }}>
                    <div>原句：{String(imp.original ?? "")}</div>
                    <div style={{ color: "var(--muted)" }}>{String(imp.explanation_cn ?? "")}</div>
                    <div>练习句：{String(imp.practice_sentence ?? "")}</div>
                    {imp.disputed ? (
                      <div className="text-xs mt-1" style={{ color: "var(--danger, #dc2626)" }}>已标记有误，待复核</div>
                    ) : (
                      <button onClick={() => handleDispute(String(imp.turn_id ?? ""))} className="text-xs mt-1" style={{ color: "var(--muted)" }}>有误</button>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-sm" style={{ color: "var(--muted)" }}>
                本次未生成改进建议（证据不足或表达较好）。
              </div>
            )}
          </div>
        ) : null}

        {err ? (
          <p className="text-sm mb-2" style={{ color: "var(--danger, #dc2626)" }}>
            {err}
          </p>
        ) : null}

        <div className="border-t pt-3" style={{ borderColor: "var(--line)" }}>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                sendText();
              }
            }}
            placeholder="Type your answer in English…"
            className="w-full rounded-xl border px-3 py-2 text-sm mb-2"
            style={{ borderColor: "var(--line)", minHeight: "56px" }}
          />
          <div className="flex items-center gap-3">
            <button
              onClick={() => toggleRecord("answer")}
              disabled={busy}
              className="rounded-xl px-4 py-2.5 text-sm font-medium text-white disabled:opacity-50"
              style={{ background: recording && recordMode === "answer" ? "var(--brand-strong)" : "var(--brand)" }}
            >
              {recording && recordMode === "answer" ? "停止" : "🎤 录音回答"}
            </button>
            {recording && recordMode === "answer" ? (
              <>
                <span className="text-xs" style={{ color: elapsed >= 50 ? "var(--danger, #dc2626)" : "var(--muted)" }}>{timerText()}</span>
                <button onClick={cancelRecord} className="text-xs px-2 py-1 rounded-lg border" style={{ borderColor: "var(--line)", color: "var(--danger, #dc2626)" }}>取消</button>
              </>
            ) : null}
            <button
              onClick={handleHint}
              disabled={busy}
              className="rounded-xl px-3 py-2.5 text-sm border"
              style={{ borderColor: "var(--line)", color: "var(--muted)" }}
            >
              💡 提示
            </button>
            <button
              onClick={sendText}
              disabled={busy || !text.trim()}
              className="rounded-xl px-4 py-2.5 text-sm font-medium text-white disabled:opacity-50"
              style={{ background: "var(--ink)" }}
            >
              发送
            </button>
            <button
              onClick={end}
              disabled={busy}
              className="ml-auto rounded-xl px-3 py-2.5 text-sm border"
              style={{ borderColor: "var(--line)", color: "var(--muted)" }}
            >
              结束练习
            </button>
          </div>
        </div>
      </div>
    </main>
  );
}
