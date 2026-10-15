"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { clearMemories, deleteMemory, getMemories, getMemorySettings, toggleMemory, updateMemory } from "@/lib/api";
import type { MemoryItem } from "@/lib/types";
import Icon from "@/components/Icon";

export default function MemoryPage() {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    let active = true;
    Promise.all([getMemories(), getMemorySettings()])
      .then(([memories, settings]) => { if (active) { setItems(memories); setEnabled(settings.enabled); } })
      .catch(e => { if (active) setErr(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  async function reload() {
    setLoading(true); setErr("");
    try { const [memories, settings] = await Promise.all([getMemories(), getMemorySettings()]); setItems(memories); setEnabled(settings.enabled); }
    catch(e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setLoading(false); }
  }

  async function onToggle(e: React.ChangeEvent<HTMLInputElement>) {
    setErr(""); setBusy(true);
    try { const r = await toggleMemory(e.target.checked); setEnabled(r.enabled); }
    catch(e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  async function onDelete(id: number) {
    if (!window.confirm("确定删除这条学习记忆？删除后不再用于后续练习。")) return;
    setBusy(true); setErr("");
    try { await deleteMemory(id); await reload(); }
    catch(e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  async function onClear() {
    if (!window.confirm("确定清空全部学习记忆？此操作不可撤销，不会删除练习历史。")) return;
    setBusy(true); setErr("");
    try { await clearMemories(); await reload(); }
    catch(e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  return (
    <main className="page">
      <div className="topline"><span className="topline-title"><Icon name="book" size={15} /> 我的学习空间 / 学习记忆</span><span className="subtle-tag"><Icon name="shield" size={12} /> 由你掌控</span></div>
      <div className="eyebrow">LEARNING, ON YOUR TERMS</div>
      <h1 className="page-title">记住什么，由你决定。</h1><p className="page-description">让有依据的表达建议陪伴后续练习，也保留随时说“不”的自由。</p>
      {err && <div className="error-banner" role="alert">{err}<div className="flex gap-3 mt-2"><button onClick={reload} className="text-link">重新加载</button><Link href="/" className="text-link">返回首页验证凭证</Link></div></div>}
      <div className="memory-layout"><div>
        <section className="panel memory-setting">
          <span className="icon-tile"><Icon name="book" /></span>
          <div><h2>学习记忆</h2><p>{enabled === null ? "正在读取设置…" : enabled ? "已开启 · 后续练习可参考有效记忆" : "已关闭 · 后续练习不调用学习记忆"}</p></div>
          <label className="toggle"><input aria-label="开启学习记忆" type="checkbox" checked={enabled === true} disabled={loading || busy || enabled === null} onChange={onToggle} /><span /></label>
        </section>
        <p className="text-xs muted mt-3 px-1">默认关闭。开启后，练习中的改进表达可能成为候选记忆。</p>
        <div className="section-head"><h2>我的表达记忆{!loading && !err ? <span className="badge badge-neutral ml-2">{items.length}</span> : null}</h2>{items.length > 0 && <button onClick={onClear} disabled={busy || loading} className="btn btn-ghost btn-small"><Icon name="trash" size={14} />清空全部</button>}</div>
        {loading ? <div role="status" aria-label="加载学习记忆"><div className="skeleton" /><div className="skeleton" /></div> : !err && items.length === 0 ? <div className="panel empty-state"><span className="icon-tile"><Icon name="leaf" size={31} /></span><h2>这里，留给慢慢积累的表达</h2><p>目前暂无学习记忆。你可以先安心练习，是否开启记忆，随时都能决定。</p><Link href="/" className="btn btn-secondary">去练习 <Icon name="arrow" size={15} /></Link></div> : <div className="history-list">{items.map(m => <article key={m.id} className="panel memory-item"><div className="flex-1 min-w-0"><span className="badge badge-neutral mb-2">{m.status === "usable" ? "有效记忆" : m.status === "candidate" ? "候选记忆" : m.status === "disputed" ? "待复核" : "待确认"} · {m.evidence_count} 条依据</span><p>{m.text}</p><p className="text-xs muted mt-2">{m.usage_reason}；来源练习 {m.source_session_id ? "可在练习历史核对" : "待核对"}；到期 {m.expires_at ? new Date(m.expires_at).toLocaleDateString("zh-CN") : "待核对"}</p><button disabled={busy} className="btn btn-ghost btn-small" onClick={async () => { const text = window.prompt("更正这条表达记忆", m.text); if (!text?.trim()) return; setBusy(true); try { await updateMemory(m.id, text); await reload(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); } }}>更正</button></div><button onClick={() => onDelete(m.id)} disabled={busy} className="btn btn-ghost btn-small" aria-label="删除这条记忆"><Icon name="trash" size={16} /></button></article>)}</div>}
      </div><aside className="panel privacy-card"><Icon name="shield" size={30} /><h2>为学习留下有用的部分</h2><p className="mb-5">练习历史和学习记忆是两回事。你可以回看对话，不必开启长期记忆。</p><div className="privacy-item"><Icon name="check" size={16} /><p>默认关闭<br />你主动开启后，才会积累表达记忆。</p></div><div className="privacy-item"><Icon name="check" size={16} /><p>随时关闭<br />关闭后，不再用于后续练习。</p></div><div className="privacy-item"><Icon name="check" size={16} /><p>支持删除<br />单条移除，或一次清空全部记忆。</p></div></aside></div>
      <footer className="page-footer"><span><Icon name="shield" size={12} /> 你的表达，为你所用</span><span>Keep what helps. Let go of the rest.</span></footer>
    </main>
  );
}
