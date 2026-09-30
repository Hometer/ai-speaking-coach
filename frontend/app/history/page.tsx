"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { addToQueue, createSession, deleteAllSessions, deleteSession, getSession, listSessions } from "@/lib/api";
import type { SessionDetail, SessionListItem } from "@/lib/types";
import Icon from "@/components/Icon";

const EVAL_CN: Record<string, string> = { independent: "独立完成", hinted: "提示下完成", incomplete: "未完成", insufficient_evidence: "证据不足", text_completed: "文字完成", not_started: "未开始", revised: "待重算" };
function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN", { month:"long", day:"numeric", hour:"2-digit", minute:"2-digit" }) : "日期未知";
}

export default function HistoryPage() {
  const router = useRouter();
  const [items, setItems] = useState<SessionListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [pending, setPending] = useState("");
  const [added, setAdded] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState<{ status?: string; practice_type?: string }>({});
  const detailRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true;
    listSessions(filter).then(data => { if (active) setItems(data); }).catch(e => { if (active) setErr(e instanceof Error ? e.message : String(e)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [filter]);

  useEffect(() => { if (detail) detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }); }, [detail]);

  async function reload() {
    setLoading(true); setErr("");
    try { setItems(await listSessions(filter)); } catch(e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setLoading(false); }
  }

  async function view(id: string) {
    setErr(""); setPending(id);
    try { setDetail(await getSession(id)); } catch(e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setPending(""); }
  }

  async function del(id: string) {
    if (!window.confirm("删除这条练习记录及其关联内容？此操作不可撤销。")) return;
    setPending(id); setErr("");
    try { await deleteSession(id); if (detail?.session_id === id) setDetail(null); await reload(); }
    catch(e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setPending(""); }
  }

  async function delAll() {
    if (!window.confirm("确定删除全部历史记录及关联内容？此操作不可撤销。")) return;
    setPending("all"); setErr("");
    try { await deleteAllSessions(); setDetail(null); await reload(); }
    catch(e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setPending(""); }
  }

  async function addQueue(sessionId: string, turnId: string) {
    setPending("queue"); setErr("");
    try { await addToQueue(sessionId, turnId); setAdded((prev) => new Set(prev).add(turnId)); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setPending(""); }
  }

  async function retry(level = "basic", difficulty = "standard", sceneId = "job_interview") {
    if (pending) return;
    setPending("retry"); setErr("");
    try { const s = await createSession(level, difficulty, 5, sceneId); router.push(`/practice/${s.session_id}`); }
    catch(e) { setErr(e instanceof Error ? e.message : String(e)); setPending(""); }
  }

  const totalTurns = items.reduce((sum,it) => sum + it.turn_count, 0);
  const endedCount = items.filter(it => it.status === "ENDED").length;

  return (
    <main className="page">
      <div className="topline"><span className="topline-title"><Icon name="history" size={15} /> 我的学习空间 / 练习历史</span><Link href="/" className="text-link">开始新的练习 <Icon name="arrow" size={14} /></Link></div>
      <div className="eyebrow">YOUR PRACTICE JOURNAL</div>
      <h1 className="page-title">每一次开口，都值得回看。</h1><p className="page-description">留住练习中的思考，带着新的发现，再试一次。</p>
      {!loading && !err && <div className="stat-grid">
        <div className="panel stat-card"><span className="icon-tile"><Icon name="history" /></span><div><strong>{items.length}</strong><p>现有练习记录</p></div></div>
        <div className="panel stat-card"><span className="icon-tile"><Icon name="chat" /></span><div><strong>{totalTurns}</strong><p>累计回答轮次</p></div></div>
        <div className="panel stat-card"><span className="icon-tile"><Icon name="check" /></span><div><strong>{endedCount}</strong><p>已结束的练习</p></div></div>
      </div>}
      <div className="section-head"><h2>我的练习记录</h2><div className="flex gap-2 flex-wrap"><button onClick={() => setFilter({})} className={`btn ${!filter.status && !filter.practice_type ? "btn-secondary" : "btn-ghost"} btn-small`}>全部</button><button onClick={() => setFilter(f => ({ ...f, status: "completed" }))} className={`btn ${filter.status === "completed" ? "btn-secondary" : "btn-ghost"} btn-small`}>已完成</button><button onClick={() => setFilter(f => ({ ...f, status: "incomplete" }))} className={`btn ${filter.status === "incomplete" ? "btn-secondary" : "btn-ghost"} btn-small`}>进行中</button>{items.length > 0 && <button onClick={delAll} disabled={!!pending || loading} className="btn btn-ghost btn-small"><Icon name="trash" size={14} />删除全部</button>}</div></div>
      {err && <div className="error-banner" role="alert">{err}<div className="flex gap-3 mt-2"><button className="text-link" onClick={reload}>重新加载</button><Link className="text-link" href="/">返回首页验证凭证</Link></div></div>}
      {loading ? <div role="status" aria-label="加载练习记录"><div className="skeleton" /><div className="skeleton" /><div className="skeleton" /></div> : items.length === 0 && !err ? (
        <div className="panel empty-state"><span className="icon-tile"><Icon name="history" size={30} /></span><h2>你的故事，从第一次练习开始</h2><p>完成一段对话后，练习记录和回顾会出现在这里。给自己 5 分钟，试着说说你的经历吧。</p><Link href="/" className="btn btn-primary">去开始练习 <Icon name="arrow" size={16} /></Link></div>
      ) : <div className="history-list">{items.map(it => <article key={it.session_id} className="panel history-row">
        <span className="icon-tile"><Icon name="briefcase" /></span><div className="history-info"><h3>{it.scene_label || "口语练习"}</h3><div className="history-meta"><span>{formatDate(it.started_at)}</span><span>{it.difficulty === "simple" ? "简单" : "标准"}难度</span><span>{it.turn_count} 轮回答</span></div></div>
        <span className={`badge ${it.goal_status === "independent" ? "" : "badge-neutral"}`}>{it.status === "PAUSED" ? "已暂停" : it.status !== "ENDED" ? "进行中" : EVAL_CN[it.goal_status] || it.goal_status}</span>
        <div className="row-actions"><button onClick={() => view(it.session_id)} disabled={!!pending} className="btn btn-secondary btn-small">{pending === it.session_id ? "加载中…" : "查看回顾"}</button><button onClick={() => retry(it.level, it.difficulty, it.scene_id)} disabled={!!pending} className="btn btn-ghost btn-small">再练一次<Icon name="arrow" size={13} /></button><button onClick={() => del(it.session_id)} disabled={!!pending} className="btn btn-ghost btn-small" aria-label={`删除 ${formatDate(it.started_at)} 的记录`}><Icon name="trash" size={15} /></button></div>
      </article>)}</div>}
      {detail && <section ref={detailRef} className="panel detail-panel" aria-label="对话与回顾">
        <div className="section-head mt-0"><div><div className="eyebrow">SESSION REVIEW</div><h2>对话与回顾</h2></div><button onClick={() => setDetail(null)} className="btn btn-ghost btn-small"><Icon name="close" size={16} />收起</button></div>
        <div className="notice mb-5"><Icon name="target" size={17} /><span>{detail.goal_text}</span></div>
        {detail.turns.map(t => <div key={t.turn_id}>
          {t.input_type === "coach" ? <div className="detail-turn"><small>AI 教练</small>{t.reply_text}</div> : t.input_type === "practice" ? <div className="detail-turn user"><small>跟读练习</small>{t.practice_sentence || "短句练习"}</div> : <>{(t.revised_text || t.user_text || t.transcript) && <div className="detail-turn user"><small>你的回答{t.revised_text ? " · 已修订" : ""}</small>{t.revised_text || t.user_text || t.transcript}</div>}{t.reply_text && <div className="detail-turn"><small>AI 教练</small>{t.reply_text}</div>}</>}
        </div>)}
        {detail.review && Array.isArray(detail.review.improvements) && detail.review.improvements.length > 0 && <div className="mt-5"><h3 className="font-semibold mb-2">值得再练的表达</h3>{(detail.review.improvements as Record<string, unknown>[]).map((imp,i) => { const tid = String(imp.turn_id ?? ""); return <div className="detail-turn" key={i}><small>{imp.disputed ? "已标记有误，待复核" : "改进建议"}</small><p>{String(imp.explanation_cn ?? "")}</p><p className="mt-2 font-medium">{String(imp.practice_sentence ?? "")}</p>{tid && !imp.disputed && <button onClick={() => addQueue(detail.session_id, tid)} disabled={!!pending || added.has(tid)} className="btn btn-secondary btn-small mt-2">{added.has(tid) ? "已加入待复练" : "加入待复练"}<Icon name="check" size={13} /></button>}</div>; })}</div>}
        <div className="flex flex-wrap gap-3 mt-5"><Link className="btn btn-secondary" href={`/practice/${detail.session_id}`}>{detail.status === "ENDED" ? "打开完整回顾" : "回到这次练习"}<Icon name="arrow" size={15} /></Link><button onClick={() => retry(detail.level, detail.difficulty, detail.scene_id)} disabled={!!pending} className="btn btn-primary">再练一次（同难度新题）</button></div>
        <p className="text-xs muted mt-4">反馈仅供练习参考，不代表考试成绩或语言能力认证。</p>
      </section>}
      <footer className="page-footer"><span><Icon name="shield" size={12} /> 会话文本保留 30 天，你也可以随时删除</span><span>Progress starts with practice.</span></footer>
    </main>
  );
}
