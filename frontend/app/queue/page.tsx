"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { deleteQueueItem, disputeQueueItem, listQueue, retestQueueItem, skipQueueItem } from "@/lib/api";
import type { QueueItem } from "@/lib/types";
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
    const a = (answers[id] || "").trim();
    if (!a) { setErr("请先写下你的复测回答"); return; }
    setPending(String(id)); setErr("");
    try { await retestQueueItem(id, a); setAnswers((prev) => ({ ...prev, [id]: "" })); await reload(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setPending(""); }
  }

  async function act(id: number, fn: (id: number) => Promise<unknown>) {
    setPending(String(id)); setErr("");
    try { await fn(id); await reload(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setPending(""); }
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
          {items.map((it) => (
            <article key={it.id} className="panel history-row">
              <span className="icon-tile"><Icon name="check" /></span>
              <div className="history-info">
                <h3>{it.suggestion_sentence || "（无建议句）"}</h3>
                <div className="history-meta">
                  <span>{STATUS_CN[it.status] || it.status}</span>
                  {it.due_at && <span>到期 {new Date(it.due_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>}
                  {it.success_count > 0 && <span>独立成功 {it.success_count} 次</span>}
                </div>
              </div>
              <span className={`badge ${it.status === "mastered" ? "" : "badge-neutral"}`}>{STATUS_CN[it.status] || it.status}</span>
              <div className="row-actions">
                {it.status === "pending" || it.status === "awaiting_retest" ? (
                  <>
                    <input className="field field-small" placeholder="写下复测回答" value={answers[it.id] || ""} onChange={(e) => setAnswers((prev) => ({ ...prev, [it.id]: e.target.value }))} />
                    <button onClick={() => retest(it.id)} disabled={!!pending} className="btn btn-secondary btn-small">{pending === String(it.id) ? "提交中…" : "复测"}</button>
                    <button onClick={() => act(it.id, skipQueueItem)} disabled={!!pending} className="btn btn-ghost btn-small">跳过</button>
                  </>
                ) : null}
                <button onClick={() => act(it.id, disputeQueueItem)} disabled={!!pending} className="btn btn-ghost btn-small">有误</button>
                <button onClick={() => act(it.id, deleteQueueItem)} disabled={!!pending} className="btn btn-ghost btn-small" aria-label="删除"><Icon name="trash" size={15} /></button>
              </div>
            </article>
          ))}
        </div>
      )}
      {active.length > 0 && <p className="text-xs muted mt-4">每次首页只推荐一项到期复练；独立说对两次（间隔至少 24 小时）才算掌握。</p>}
      <footer className="page-footer"><span><Icon name="shield" size={12} /> 复练数据随来源删除而失效</span><span>Progress starts with practice.</span></footer>
    </main>
  );
}
