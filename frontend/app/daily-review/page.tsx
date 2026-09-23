"use client";

import { useEffect, useState } from "react";
import { dailyReviewAction, dailyReviewAttempt, dailyReviewView, dueToday, startDailyReview } from "@/lib/api";
import type { DailyReviewCurrent, DueToday } from "@/lib/types";
import Icon from "@/components/Icon";

export default function DailyReviewPage() {
  const [due, setDue] = useState<DueToday | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [current, setCurrent] = useState<DailyReviewCurrent | null>(null);
  const [answer, setAnswer] = useState("");
  const [showHint, setShowHint] = useState(false);
  const [helpUsed, setHelpUsed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  useEffect(() => {
    dueToday().then(setDue).catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);

  async function begin() {
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const r = await startDailyReview();
      if (!r.session_id) {
        setMsg("今天暂无到期复练内容。");
        return;
      }
      setSessionId(r.session_id);
      setCurrent(await dailyReviewView(r.session_id));
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
      const r = await dailyReviewAttempt(sessionId, answer.trim(), withHelp);
      setAnswer("");
      setShowHint(false);
      setHelpUsed(false);
      setMsg(r.success ? (withHelp ? "完成（用了提示）" : "完成，已独立表达") : "没对上，再试一次。");
      setCurrent(r.next);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function act(a: "skip" | "defer" | "dispute" | "end") {
    if (!sessionId) return;
    setBusy(true);
    setErr("");
    try {
      const r = await dailyReviewAction(sessionId, a);
      setCurrent(r.next);
      setAnswer("");
      setShowHint(false);
      setHelpUsed(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const item = current?.item;
  const summary = current?.summary;

  return (
    <main className="page">
      <section className="panel">
        <div className="section-head">
          <h2><Icon name="target" size={19} /> 今日复练</h2>
          <span className="badge">一次完成 3~5 条到期内容</span>
        </div>

        {err && <p className="error-banner" role="alert">{err}</p>}
        {msg && <div className="notice mb-3"><Icon name="check" size={17} /><p>{msg}</p></div>}

        {!current && due && (
          due.count === 0 ? (
            <p className="muted">今天暂无到期复练内容，可以先去练习或稍后再来。</p>
          ) : (
            <div>
              <p className="mb-3">今天有 <strong>{due.count}</strong> 条到期内容，预计 3~5 分钟。</p>
              <button onClick={begin} disabled={busy} className="btn btn-primary"><Icon name="arrow" size={16} />开始今日复练</button>
            </div>
          )
        )}

        {current && item && !current.done && (
          <div className="mt-4">
            <p className="text-sm muted">第 {current.index + 1} / 共 {current.total} 条</p>
            <div className="mt-2 p-3" style={{ background: "#f4f8f1", borderRadius: 8 }}>
              <p className="text-sm muted">先回忆这句话，卡住了再要提示：</p>
              {showHint && <p className="mt-2"><strong>{item.suggestion_sentence}</strong></p>}
            </div>
            <div className="mt-3">
              <textarea value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="用英文说出这句话…" disabled={busy} maxLength={2000} style={{ width: "100%", minHeight: "72px", padding: "10px 12px", borderRadius: 8, border: "1px solid #d7ded4", fontSize: 15, resize: "vertical" }} />
            </div>
            <div className="flex flex-wrap gap-2 mt-3">
              {!showHint && <button onClick={() => { setShowHint(true); setHelpUsed(true); }} disabled={busy} className="btn btn-secondary">要提示</button>}
              <button onClick={() => submit(helpUsed)} disabled={busy || !answer.trim()} className="btn btn-primary">提交</button>
              <button onClick={() => act("skip")} disabled={busy} className="btn btn-ghost">跳过</button>
              <button onClick={() => act("defer")} disabled={busy} className="btn btn-ghost">延后</button>
              <button onClick={() => act("dispute")} disabled={busy} className="btn btn-ghost">标记有误</button>
            </div>
            <p className="text-xs muted mt-3">用了关键词/半句/完整答案会回到 1 天间隔；独立完成进下一档间隔。</p>
          </div>
        )}

        {current?.done && summary && (
          <div className="notice mt-4">
            <Icon name="check" size={19} />
            <div>
              <p><strong>今日复练结束</strong></p>
              <p className="text-sm">完成 {summary.completed} 条（其中用提示 {summary.helped} 条）、跳过 {summary.skipped} 条、延后 {summary.deferred} 条。</p>
            </div>
          </div>
        )}
      </section>
    </main>
  );
}
