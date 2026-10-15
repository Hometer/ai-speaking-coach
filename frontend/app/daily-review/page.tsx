"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import Icon from "@/components/Icon";
import { clearIntentKey, dailyReviewAction, dailyReviewAttempt, dailyReviewView, dueToday, getInviteCode, intentKey, recordProductEvent, revealQueueItem, startDailyReview } from "@/lib/api";
import type { DailyReviewCurrent, DueToday } from "@/lib/types";
import styles from "./page.module.css";

export default function DailyReviewPage() {
  const [due, setDue] = useState<DueToday | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [current, setCurrent] = useState<DailyReviewCurrent | null>(null);
  const [answer, setAnswer] = useState("");
  const [showHint, setShowHint] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [recording, setRecording] = useState(false);
  const recRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const recordTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mounted = useRef(true);
  const recordSession = useRef("");
  function cancelRecording() {
    if (recordTimeout.current) clearTimeout(recordTimeout.current);
    if (recRef.current) { recRef.current.onstop = null; if (recRef.current.state === "recording") { recRef.current.stop(); if (recordSession.current) recordProductEvent(recordSession.current, "record_cancel"); } }
    streamRef.current?.getTracks().forEach(track => track.stop());
    if (mounted.current) setRecording(false);
  }
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; cancelRecording(); }; }, []);


  useEffect(() => {
    let active = true;
    const saved = sessionStorage.getItem("active-daily-review");
    if (saved) void dailyReviewView(saved).then(view => { if (active && !view.done) { setSessionId(saved); setCurrent(view); } else if (active && view.done) { if(view.notice)setMsg(view.notice); sessionStorage.removeItem("active-daily-review"); clearIntentKey("daily-start"); } }).catch(() => sessionStorage.removeItem("active-daily-review"));
    dueToday()
      .then((data) => { if (active) setDue(data); })
      .catch((e) => { if (active) setErr(e instanceof Error ? e.message : String(e)); });
    return () => { active = false; };
  }, []);

  async function begin() {
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const result = await startDailyReview();
      if (!result.session_id) {
        setMsg("今天暂无到期复练内容。");
        return;
      }
      sessionStorage.setItem("active-daily-review", result.session_id);
      setSessionId(result.session_id);
      setCurrent(await dailyReviewView(result.session_id));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function submit(withHelp: boolean) {
    if (!sessionId || !answer.trim()) return;
    setBusy(true);
    setErr("");
    try {
      const result = await dailyReviewAttempt(sessionId, answer.trim(), withHelp, "text");
      setAnswer("");
      setShowHint(false);
      setMsg(result.result === "recall_completed" ? "回忆完成。接下来换一个条件说，成功后才推进间隔。" : result.result === "text_completed" ? "已完成文字练习，不计入口语独立。" : result.result === "hinted" ? "已完成提示下练习。" : result.success ? (withHelp ? "这一条已完成，记录为使用提示。" : "这一条已独立完成。") : "还没有对上，可以再试一次或使用提示。");
      if (result.next.done) { clearIntentKey("daily-start"); sessionStorage.removeItem("active-daily-review"); }
      setCurrent(result.next.done ? await dailyReviewView(sessionId) : result.next);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function reveal() {
    if (!item) return;
    setBusy(true);
    setErr("");
    try {
      await revealQueueItem(item.id);
      setShowHint(true);
      const view = await dailyReviewView(sessionId!);
      setCurrent(view);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function toggleRecord() {
    if (busy || !sessionId) return;
    if (recRef.current && recRef.current.state === "recording") { recRef.current.stop(); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      if (!mounted.current) { stream.getTracks().forEach(t => t.stop()); return; }
      streamRef.current = stream;
      chunksRef.current = [];
      const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
      const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      recRef.current = rec;
      rec.ondataavailable = (e) => { if (e.data.size) chunksRef.current.push(e.data); };
      rec.onstop = () => {
        if (recordTimeout.current) clearTimeout(recordTimeout.current);
        stream.getTracks().forEach((t) => t.stop());
        setRecording(false);
        const blob = new Blob(chunksRef.current, { type: rec.mimeType || "audio/webm" });
        submitVoice(blob);
      };
      rec.start();
      recordSession.current = sessionId;
      recordProductEvent(sessionId, "record_start");
      recordTimeout.current = setTimeout(() => { if (rec.state === "recording") rec.stop(); }, 60_000);
      setRecording(true);
    } catch {
      setErr("无法获取麦克风，可用文字输入。");
    }
  }

  async function submitVoice(blob: Blob) {
    if (!sessionId) return;
    setBusy(true);
    setErr("");
    try {
      const fd = new FormData();
      fd.append("audio", blob, "daily-review");
      const scope = `daily:${sessionId}`;
      fd.append("attempt_key", await intentKey(scope, blob));
      const res = await fetch(`/api/v1/review-queue/daily-review/${sessionId}/attempt/voice`, {
        method: "POST",
        headers: { "X-Invite-Code": getInviteCode() },
        body: fd,
      });
      const data = await res.json();
      if (!res.ok) { setErr(data?.error?.message ?? `HTTP ${res.status}`); return; }
      clearIntentKey(scope);
      setShowHint(false);
      setMsg(data.result === "recall_completed" ? "回忆完成，接下来请回答换题条件。" : data.success ? "这一条已独立完成（语音）。" : "还没有对上，可以再试一次或使用提示。");
      if (data.next.done) { clearIntentKey("daily-start"); sessionStorage.removeItem("active-daily-review"); }
      setCurrent(data.next.done ? await dailyReviewView(sessionId) : data.next);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function act(action: "skip" | "defer" | "dispute" | "end") {
    if (!sessionId) return;
    cancelRecording();
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const result = await dailyReviewAction(sessionId, action);
      if (action === "end") { clearIntentKey("daily-start"); sessionStorage.removeItem("active-daily-review"); }
      if (result.next.done) { clearIntentKey("daily-start"); sessionStorage.removeItem("active-daily-review"); }
      setCurrent(result.next.done ? await dailyReviewView(sessionId) : result.next);
      setAnswer("");
      setShowHint(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const item = current?.item;
  const summary = current?.summary;
  const progress = current?.total ? Math.round(((current.index + 1) / current.total) * 100) : 0;

  return (
    <main className="page">
      <div className="topline">
        <span className="topline-title"><Icon name="target" size={15} /> 我的学习空间 / 今日复练</span>
        <Link href="/queue" className="text-link">查看待复练 <Icon name="arrow" size={14} /></Link>
      </div>
      <div className="eyebrow">TODAY&apos;S REVIEW</div>
      <h1 className="page-title">把到期的表达，再说得稳一点。</h1>
      <p className="page-description">一次只练今天真正到期的内容。卡住可以看提示，退出和服务故障都不会记成学习失败。</p>

      {err && <div className="error-banner" role="alert">{err}</div>}
      {(msg || current?.notice) && <div className="notice mt-4" role="status"><Icon name={current?.notice ? "help" : "check"} size={17} /><p>{msg || current?.notice}</p></div>}

      {!current && due === null && !err && (
        <div className={styles.loading} role="status" aria-label="加载今日复练">
          <div className="skeleton" />
          <div className="skeleton" />
        </div>
      )}

      {!current && due && due.count === 0 && (
        <section className="panel empty-state">
          <span className="icon-tile"><Icon name="check" size={30} /></span>
          <h2>今天没有到期内容</h2>
          <p>间隔到了再练，比连续重复更有用。你可以开始一段新练习，或者去看看待复练队列。</p>
          <div className={styles.emptyActions}>
            <Link href="/" className="btn btn-primary">开始新练习 <Icon name="arrow" size={15} /></Link>
            <Link href="/queue" className="btn btn-secondary">查看待复练</Link>
          </div>
        </section>
      )}

      {!current && due && due.count > 0 && (
        <section className={`panel ${styles.startPanel}`}>
          <div className={styles.startIcon}><Icon name="target" size={28} /></div>
          <div className={styles.startCopy}>
            <span className="badge">今日已到期</span>
            <h2>{due.count} 条表达，约 3~5 分钟</h2>
            <p>系统按逾期时间和帮助记录排序。少于 3 条时按实际数量展示，不会拿未到期内容凑数。</p>
          </div>
          <button onClick={begin} disabled={busy} className="btn btn-primary">
            <Icon name="arrow" size={16} />{busy ? "正在准备…" : "开始今日复练"}
          </button>
        </section>
      )}

      {current && item && !current.done && (
        <section className={`panel ${styles.practicePanel}`}>
          <header className={styles.practiceHeader}>
            <div>
              <span className="eyebrow">RECALL FIRST</span>
              <h2>先回忆，再开口</h2>
            </div>
            <span className="badge">第 {current.index + 1} / {current.total} 条</span>
          </header>

          <div className={styles.progressTrack} aria-label={`今日复练进度 ${progress}%`} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}>
            <span style={{ width: `${progress}%` }} />
          </div>

          <div className={styles.promptCard}>
            <span className={styles.promptIcon}><Icon name={showHint ? "book" : "help"} size={20} /></span>
            <div>
              <p className={styles.promptLabel}>{showHint ? "提示已经展开" : "先不要看答案"}</p>
              {showHint ? <p className={styles.hintText}>{item.suggestion_sentence}</p> : <p>{item.question_text ? item.question_text : item.meaning_zh ? `想表达：${item.meaning_zh}` : "想一想这条表达原本想传达什么，再用自己的话说出来。"}</p>}
            </div>
          </div>

          <div className={styles.answerBlock}>
            <label htmlFor="daily-review-answer">你的英文回答</label>
            <textarea
              id="daily-review-answer"
              className="field"
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              placeholder="用英文说出这句话，意思对即可…"
              disabled={busy}
              maxLength={2000}
            />
          </div>

          <div className={styles.primaryActions}>
            {!showHint && <button onClick={reveal} disabled={busy} className="btn btn-secondary"><Icon name="help" size={15} />看提示</button>}
            <button onClick={toggleRecord} disabled={busy} className="btn btn-primary"><Icon name={recording ? "stop" : "mic"} size={15} />{recording ? "停止并提交" : "录音回忆"}</button>
            {recording && <button onClick={cancelRecording} className="btn btn-ghost">取消录音</button>}
            <button onClick={() => submit(false)} disabled={busy || recording || !answer.trim()} className="btn btn-secondary">提交文字<Icon name="arrow" size={15} /></button>
          </div>

          <div className={styles.secondaryActions}>
            <button onClick={() => act("skip")} disabled={busy} className="btn btn-ghost btn-small">跳过</button>
            <button onClick={() => act("defer")} disabled={busy} className="btn btn-ghost btn-small">延后</button>
            <button onClick={() => act("dispute")} disabled={busy} className="btn btn-ghost btn-small">内容有误</button>
            <button onClick={() => act("end")} disabled={busy} className="btn btn-ghost btn-small">结束本次复练</button>
          </div>
          <p className={styles.ruleNote}>使用提示会回到较短间隔；独立完成才会进入下一档。退出、识别失败或服务故障不会记作失败。</p>
        </section>
      )}

      {current?.done && summary && (
        <section className={`panel ${styles.summaryPanel}`}>
          <span className={styles.summaryIcon}><Icon name="check" size={30} /></span>
          <div>
            <span className="eyebrow">REVIEW COMPLETE</span>
            <h2>今天的复练完成了</h2>
            <p>完成 {summary.completed} 条，其中使用提示 {summary.helped} 条；跳过 {summary.skipped} 条，延后 {summary.deferred} 条。</p>
          </div>
          <Link href="/queue" className="btn btn-secondary">回到待复练</Link>
        </section>
      )}

      <footer className="page-footer"><span><Icon name="shield" size={12} /> 只安排已到期且来源有效的内容</span><span>Review less. Remember more.</span></footer>
    </main>
  );
}
