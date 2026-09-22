"use client";

import { useEffect, useState } from "react";
import { qaAddLabel, qaSessions, qaStats } from "@/lib/api";
import type { QaSession } from "@/lib/types";
import Icon from "@/components/Icon";

const ISSUE_TYPES = [
  { value: "mis_correction", label: "误纠错" },
  { value: "wrong_completion", label: "错误完成" },
  { value: "irrelevant_followup", label: "追问无关" },
  { value: "help_skip", label: "帮助越级" },
  { value: "insufficient_evidence", label: "证据不足" },
  { value: "inappropriate", label: "不当内容" },
];

export default function QaPage() {
  const [sessions, setSessions] = useState<QaSession[]>([]);
  const [stats, setStats] = useState<{ total: number; by_type: Record<string, number> }>({ total: 0, by_type: {} });
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [pending, setPending] = useState("");
  const [choice, setChoice] = useState<Record<string, string>>({});

  useEffect(() => {
    let active = true;
    Promise.all([qaSessions(), qaStats()])
      .then(([s, st]) => { if (active) { setSessions(s); setStats(st); } })
      .catch((e) => { if (active) setErr(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  async function label(sessionId: string, turnId: string) {
    const t = choice[turnId] || "mis_correction";
    setPending(turnId); setErr("");
    try { await qaAddLabel(sessionId, turnId, t); setStats(await qaStats()); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setPending(""); }
  }

  return (
    <main className="page">
      <div className="topline"><span className="topline-title"><Icon name="shield" size={15} /> 内部质检工作台</span><span className="subtle-tag">仅质检员可见</span></div>
      <div className="eyebrow">QUALITY REVIEW</div>
      <h1 className="page-title">质检工作台</h1>
      <p className="page-description">脱敏查看会话、标记问题类型，标记结果进入版本统计。默认不播放录音。</p>
      {err && <div className="error-banner" role="alert">{err}</div>}
      {loading ? (
        <div role="status" aria-label="加载质检数据"><div className="skeleton" /><div className="skeleton" /></div>
      ) : (
        <>
          <div className="panel panel-padding mb-5">
            <span className="field-label">版本统计</span>
            <p className="mt-2">共标记 {stats.total} 条</p>
            {Object.keys(stats.by_type).length > 0 && (
              <div className="flex flex-wrap gap-2 mt-2">
                {Object.entries(stats.by_type).map(([k, v]) => <span key={k} className="badge badge-neutral">{k} × {v}</span>)}
              </div>
            )}
          </div>
          {sessions.length === 0 ? (
            <div className="panel empty-state"><h2>暂无会话数据</h2><p>有练习记录后，这里会列出脱敏会话供标记。</p></div>
          ) : (
            <div className="history-list">
              {sessions.map((s) => (
                <article key={s.session_id} className="panel">
                  <div className="flex items-center gap-3 mb-3">
                    <span className="badge">{s.scene_id}</span>
                    <span className="badge badge-neutral">{s.status}</span>
                    <span className="text-xs muted">{s.started_at ? new Date(s.started_at).toLocaleString("zh-CN") : ""}</span>
                  </div>
                  {s.turns.map((t) => (
                    <div key={t.turn_id} className="detail-turn">
                      <small>{t.input_type === "coach" ? "AI 教练" : `用户回答 · ${t.eval_result || "未评估"}`}</small>
                      <p>{t.input_type === "coach" ? t.reply_text : t.user_text || "（空）"}</p>
                      {t.input_type !== "coach" && (
                        <div className="flex gap-2 mt-2">
                          <select className="field field-small" value={choice[t.turn_id] || "mis_correction"} onChange={(e) => setChoice((prev) => ({ ...prev, [t.turn_id]: e.target.value }))}>
                            {ISSUE_TYPES.map((i) => <option key={i.value} value={i.value}>{i.label}</option>)}
                          </select>
                          <button className="btn btn-ghost btn-small" onClick={() => label(s.session_id, t.turn_id)} disabled={!!pending}>{pending === t.turn_id ? "提交中…" : "标记"}</button>
                        </div>
                      )}
                    </div>
                  ))}
                </article>
              ))}
            </div>
          )}
        </>
      )}
      <footer className="page-footer"><span><Icon name="shield" size={12} /> 默认不播放录音，未授权片段不可播放</span><span>Internal QA</span></footer>
    </main>
  );
}
