"use client";
import { apiDate } from "@/lib/dates";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { clearIntentKey, deleteQueueItem, disputeQueueItem, getInviteCode, intentKey, listQueue, recordProductEvent, retestQueueItem, revealQueueItem, skipQueueItem } from "@/lib/api";
import type { QueueItem } from "@/lib/types";
import VoiceRisk from "@/components/VoiceRisk";
import { MediaLease } from "@/lib/mediaLease";
import Icon from "@/components/Icon";

const STATUS_CN: Record<string, string> = {
  pending: "待练",
  awaiting_retest: "待复测",
  mastered: "已掌握",
  disputed: "有争议",
  invalid: "已失效",
};

export default function QueuePage() {
  const [items, setItems] = useState<QueueItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [pending, setPending] = useState("");
  const [now, setNow] = useState(0);
  const [recId, setRecId] = useState<number | null>(null);
  const recRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const recordSession = useRef("");
  const recordTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mounted = useRef(true);
  const mediaLease = useRef(new MediaLease());
  const [riskRevision, setRiskRevision] = useState(0);
  function cancelRecording() {
    mediaLease.current.cancel();
    if (recordTimeout.current) clearTimeout(recordTimeout.current);
    if (recRef.current) { recRef.current.onstop = null; if (recRef.current.state === "recording") { recRef.current.stop(); if (recordSession.current) recordProductEvent(recordSession.current, "record_cancel"); } }
    streamRef.current?.getTracks().forEach(track => track.stop());
    if (mounted.current) setRecId(null);
  }
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; cancelRecording(); }; }, []);


  useEffect(() => {
    const update = () => setNow(Date.now());
    const first = window.setTimeout(update, 0);
    const interval = window.setInterval(update, 30_000);
    return () => { window.clearTimeout(first); window.clearInterval(interval); };
  }, []);

  useEffect(() => {
    let active = true;
    listQueue()
      .then((d) => { if (active) setItems(d); })
      .catch((e) => { if (active) setErr(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  async function reload() {
    setErr("");
    try { setItems(await listQueue()); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }

  async function retest(id: number) {
    cancelRecording();
    const a = (answers[id] || "").trim();
    if (!a) { setErr("请先写下你的复测回答"); return; }
    setPending(String(id)); setErr("");
    try { await retestQueueItem(id, a); setAnswers((prev) => ({ ...prev, [id]: "" })); await reload(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setPending(""); }
  }

  async function act(id: number, fn: (id: number) => Promise<unknown>) {
    cancelRecording();
    setPending(String(id)); setErr("");
    try { await fn(id); await reload(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setPending(""); }
  }

  async function reveal(id: number) {
    await act(id, revealQueueItem);
  }

  async function toggleRecord(id: number) {
    if (pending || !id) return;
    if (recRef.current && recRef.current.state === "recording") { recRef.current.stop(); return; }
    const lease = mediaLease.current.begin();
    if (lease === null) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!mediaLease.current.accept(lease, stream)) return;
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
        setRecId(null);
        const blob = new Blob(chunksRef.current, { type: rec.mimeType || "audio/webm" });
        submitVoice(id, blob);
      };
      rec.start();
      recordSession.current = items.find(it => it.id === id)?.source_session_id || "";
      if (recordSession.current) recordProductEvent(recordSession.current, "record_start");
      recordTimeout.current = setTimeout(() => { if (rec.state === "recording") rec.stop(); }, 60_000);
      setRecId(id);
    } catch {
      if (!mediaLease.current.fail(lease)) return;
      streamRef.current?.getTracks().forEach(track => track.stop());
      setErr("无法获取麦克风，可用文字复测。");
    }
  }

  async function submitVoice(id: number, blob: Blob) {
    setPending(String(id)); setErr("");
    try {
      const fd = new FormData();
      fd.append("audio", blob, "retest");
      const scope = `retest:${id}`;
      fd.append("attempt_key", await intentKey(scope, blob));
      const res = await fetch(`/api/v1/review-queue/${id}/retest/voice`, { method: "POST", headers: { "X-Invite-Code": getInviteCode() }, body: fd });
      const data = await res.json();
      if (!res.ok) { setErr(data?.error?.message ?? `HTTP ${res.status}`); return; }
      clearIntentKey(scope);
      if (data.risk_hold) { setRiskRevision(v => v + 1); return; }
      await reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setPending("");
    }
  }

  const active = items.filter((i) => i.status === "pending" || i.status === "awaiting_retest");

  return (
    <main className="page">
      <div className="topline"><span className="topline-title"><Icon name="check" size={15} /> 我的学习空间 / 待复练</span><Link href="/" className="text-link">开始新的练习 <Icon name="arrow" size={14} /></Link></div>
      <div className="eyebrow">REVIEW QUEUE</div>
      <h1 className="page-title">值得再练的，别让它们溜走。</h1>
      <p className="page-description">把回顾里最该再练的表达放在这里，到期再练一次，练到能独立说出来。</p>
      {err && <div className="error-banner" role="alert">{err}</div>}
      {loading ? (
        <div role="status" aria-label="加载待复练"><div className="skeleton" /><div className="skeleton" /></div>
      ) : items.length === 0 ? (
        <div className="panel empty-state"><span className="icon-tile"><Icon name="check" size={30} /></span><h2>队列还是空的</h2><p>在回顾里看到想再练的表达，点「加入待复练」，它就会出现在这里。</p><Link href="/" className="btn btn-primary">去开始练习 <Icon name="arrow" size={16} /></Link></div>
      ) : (
        <div className="history-list">
          {items.map((it) => {
            const isDue = it.material_available !== false && !!it.due_at && apiDate(it.due_at).getTime() <= now;
            return (
            <article key={it.id} className="panel history-row">
              <span className="icon-tile"><Icon name="check" /></span>
              <div className="history-info">
                <h3>{it.help_shown && it.suggestion_sentence ? it.suggestion_sentence : (it.question_text || it.meaning_zh || "（暂无提示）")}</h3>
                <div className="history-meta">
                  <span>{STATUS_CN[it.status] || it.status}</span>
                  {it.due_at && <span>到期 {apiDate(it.due_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>}
                  {it.success_count > 0 && <span>独立成功 {it.success_count} 次</span>}
                </div>
              </div>
              <span className={`badge ${it.status === "mastered" ? "" : "badge-neutral"}`}>{STATUS_CN[it.status] || it.status}</span>
              <VoiceRisk scope="queue_voice" objectId={String(it.id)} revision={riskRevision} disabled={!!pending} onResolved={reload} />
              <div className="row-actions queue-actions">
                {it.status === "pending" || it.status === "awaiting_retest" ? (
                  <>
                    {isDue ? <><input className="field field-small" placeholder="写下复测回答" value={answers[it.id] || ""} onChange={(e) => setAnswers((prev) => ({ ...prev, [it.id]: e.target.value }))} />
                    <button onClick={() => retest(it.id)} disabled={!!pending || recId !== null} className="btn btn-secondary btn-small">{pending === String(it.id) ? "提交中…" : "复测"}</button>
                    <button onClick={() => toggleRecord(it.id)} disabled={!!pending || (recId !== null && recId !== it.id)} className="btn btn-primary btn-small"><Icon name={recId === it.id ? "stop" : "mic"} size={14} />{recId === it.id ? "停止" : "录音复测"}</button>
                    {recId === it.id && <button onClick={cancelRecording} className="btn btn-ghost btn-small">取消录音</button>}</> : <span className="text-xs muted">{it.material_available === false ? "复练材料待准备" : "到期后可复测"}</span>}
                    {!it.help_shown && <button onClick={() => reveal(it.id)} disabled={!!pending} className="btn btn-ghost btn-small">看答案</button>}
                    <button onClick={() => act(it.id, skipQueueItem)} disabled={!!pending} className="btn btn-ghost btn-small">跳过</button>
                  </>
                ) : null}
                <button onClick={() => act(it.id, disputeQueueItem)} disabled={!!pending} className="btn btn-ghost btn-small">有误</button>
                <button onClick={() => act(it.id, deleteQueueItem)} disabled={!!pending} className="btn btn-ghost btn-small" aria-label="删除"><Icon name="trash" size={15} /></button>
              </div>
            </article>
          ); })}
        </div>
      )}
      {active.length > 0 && <p className="text-xs muted mt-4">每次首页只推荐一项到期复练；独立说对两次（间隔至少 24 小时）才算掌握。</p>}
      <footer className="page-footer"><span><Icon name="shield" size={12} /> 复练数据随来源删除而失效</span><span>Progress starts with practice.</span></footer>
    </main>
  );
}
