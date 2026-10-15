"use client";

import { useEffect, useState } from "react";
import { qaLearningMetrics, reviewConversation, completeQaRecording, qaAddLabel, qaRecordingAccessLog, qaSessions, qaStats } from "@/lib/api";
import type { QaSession } from "@/lib/types";
import AudioPlayer from "@/components/AudioPlayer";
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
  const [learningMetrics, setLearningMetrics] = useState<Awaited<ReturnType<typeof qaLearningMetrics>> | null>(null);
  const [sessions, setSessions] = useState<QaSession[]>([]);
  const [stats, setStats] = useState<{ total: number; by_type: Record<string, number> }>({ total: 0, by_type: {} });
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [pending, setPending] = useState("");
  const [choice, setChoice] = useState<Record<string, string>>({});
  const [quality, setQuality] = useState<Record<string, Record<string, boolean>>>({});
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [selectedRecording, setSelectedRecording] = useState<number | null>(null);
  const [accessLog, setAccessLog] = useState("");

  useEffect(() => {
    let active = true;
    void qaLearningMetrics().then(m => { if(active) setLearningMetrics(m); }).catch(() => {});
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

  async function search() {
    setLoading(true); setErr("");
    try { setSessions(await qaSessions(filters)); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setLoading(false); }
  }

  async function completeRecording(id: number) {
    if (!window.confirm("确认审核完成并提前删除这段质检录音？")) return;
    setPending(`audio-${id}`); setErr("");
    try { await completeQaRecording(id); setSelectedRecording(null); await search(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setPending(""); }
  }

  async function readAccessLog(id: number) {
    setErr("");
    try {
      const rows = await qaRecordingAccessLog(id);
      setAccessLog(rows.map(r => `${r.created_at} ${r.action} ${r.outcome}`).join("；") || "暂无访问记录");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <main className="page">
      <div className="topline"><span className="topline-title"><Icon name="shield" size={15} /> 内部质检工作台</span><span className="subtle-tag">仅质检员可见</span></div>
      <div className="eyebrow">QUALITY REVIEW</div>
      <h1 className="page-title">质检工作台</h1>
      <p className="page-description">脱敏查看会话、标记问题类型，标记结果进入版本统计。默认不播放录音。</p>
      <form className="panel panel-padding mb-5 flex gap-2 flex-wrap" onSubmit={e => { e.preventDefault(); void search(); }} aria-label="质检筛选">
        <label>场景 <input className="field field-small" value={filters.scene_id || ""} onChange={e => setFilters(f => ({ ...f, scene_id: e.target.value }))} placeholder="场景 ID" /></label>
        <label>版本 <input className="field field-small" value={filters.version || ""} onChange={e => setFilters(f => ({ ...f, version: e.target.value }))} placeholder="如 v1" /></label>
        <label>输入 <select className="field field-small" value={filters.input_type || ""} onChange={e => setFilters(f => ({ ...f, input_type: e.target.value }))}><option value="">全部</option><option value="voice">语音</option><option value="text">文字</option></select></label>
        <label>帮助 <select className="field field-small" value={filters.help_type || ""} onChange={e => setFilters(f => ({ ...f, help_type: e.target.value }))}><option value="">全部</option><option value="repeat">重播</option><option value="explain">解释</option><option value="organize">整理表达</option><option value="translate">翻译</option></select></label>
        <label>评价 <select className="field field-small" value={filters.eval_result || ""} onChange={e => setFilters(f => ({ ...f, eval_result: e.target.value }))}><option value="">全部</option><option value="independent">独立</option><option value="hinted">提示下</option><option value="incomplete">未完成</option><option value="text_completed">文字完成</option></select></label>
        <label>问题 <select className="field field-small" value={filters.issue_type || ""} onChange={e => setFilters(f => ({ ...f, issue_type: e.target.value }))}><option value="">全部</option>{ISSUE_TYPES.map(i => <option key={i.value} value={i.value}>{i.label}</option>)}</select></label>
        <label>错误码 <input className="field field-small" value={filters.error_code || ""} onChange={e => setFilters(f => ({ ...f, error_code: e.target.value }))} placeholder="如 ASR_TIMEOUT" /></label>
        <button className="btn btn-secondary btn-small" type="submit" disabled={loading}>筛选</button>
      </form>
      {err && <div className="error-banner" role="alert">{err}</div>}
      {loading ? (
        <div role="status" aria-label="加载质检数据"><div className="skeleton" /><div className="skeleton" /></div>
      ) : (
        <>
          {learningMetrics && <div className="panel panel-padding mb-5"><h2>7 日主动有效复练</h2><p>满观察窗口 {learningMetrics.eligible_users} 人；主动有效复练 {learningMetrics.active_review_users} 人；{learningMetrics.active_review_rate === null ? "样本不足，待观察" : `${Math.round(learningMetrics.active_review_rate * 100)}%`}。完整场景复练 {learningMetrics.full_scene_review_users} 人；观察未满 {learningMetrics.pending_observation_users} 人。</p><p className="text-xs muted">排除文字、跟读、研究员安排和未到期复测。语音活动时长规则仍待真实样本校准。</p></div>}
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
                    <span className="badge badge-neutral">场景版 {s.scene_version}</span>
                    {s.disputed && <span className="badge badge-neutral">有争议</span>}
                    <span className="text-xs muted">{s.started_at ? new Date(s.started_at).toLocaleString("zh-CN") : ""}</span>
                  </div>
                  <p className="text-xs muted mb-2">预算预留结算 ¥{s.estimated_cost.toFixed(3)}；供应商估算 {s.supplier_estimate === null ? "费率/计量待核对" : `¥${s.supplier_estimate.toFixed(3)}`}；供应商账单 {s.billed_cost === null ? "待核对" : `¥${s.billed_cost.toFixed(3)}`}；LLM {s.provider_usage.llm_attempts} 次（输入 {s.provider_usage.llm_input_tokens} / 输出 {s.provider_usage.llm_output_tokens} token）；ASR 上传 {s.provider_usage.asr_input_bytes} 字节；TTS {s.provider_usage.tts_attempts} 次、{s.provider_usage.tts_text_chars} 字符；发音评测 {s.provider_usage.ise_attempts} 次、上传 {s.provider_usage.ise_input_bytes} 字节{ s.error_codes.length ? `；错误码 ${s.error_codes.join("、")}` : ""}</p>
                  {s.turns.map((t) => (
                    <div key={t.turn_id} className="detail-turn">
                      <small>{t.input_type === "coach" ? "AI 教练" : `用户回答 · ${t.eval_result || "未评估"}`}</small>
                      <p>{t.input_type === "coach" ? t.reply_text : t.user_text || "（空）"}</p>
                      {t.input_type !== "coach" && <p className="text-xs muted">耗时 {t.duration_ms}ms；证据版 {t.evidence_version || "未记录"}；用量 {t.usage || "未记录"}</p>}
                      {t.qa_recording_id && <div className="mt-2"><button className="btn btn-secondary btn-small" onClick={() => setSelectedRecording(t.qa_recording_id)}>播放已授权录音</button><button className="btn btn-ghost btn-small" onClick={() => void completeRecording(t.qa_recording_id!)} disabled={!!pending}>审核完成并删除录音</button><button className="btn btn-ghost btn-small" onClick={() => void readAccessLog(t.qa_recording_id!)}>查看访问记录</button>{selectedRecording === t.qa_recording_id && <AudioPlayer src={`/api/v1/qa/recordings/${t.qa_recording_id}`} />}</div>}
                      {t.conversation_quality && <div className="mt-2">
                        <p className="text-xs muted">对话质检：{t.conversation_quality.status === "reviewed" ? "已审核" : "未审核，未知不按正常计"}；用户直接提问 {t.conversation_quality.direct_question ? "是" : "否"}；提示词 {t.conversation_quality.prompt_version}</p>
                        {([['answered','是否回应提问'],['repeated_followup','是否机械重复追问'],['topic_jump','是否突兀换题']] as const).map(([key,label]) => <label key={key} className="mr-2 text-xs">{label}<select className="field field-small" value={quality[t.turn_id]?.[key] === undefined ? (t.conversation_quality?.[key] === null ? "" : String(t.conversation_quality?.[key])) : String(quality[t.turn_id][key])} onChange={e=>setQuality(q=>({...q,[t.turn_id]:{...q[t.turn_id],[key]:e.target.value === "true"}}))}><option value="">待判断</option><option value="true">是</option><option value="false">否</option></select></label>)}
                        <button disabled={!!pending} className="btn btn-ghost btn-small" onClick={async()=>{const saved=t.conversation_quality;const values={answered:saved?.answered,repeated_followup:saved?.repeated_followup,topic_jump:saved?.topic_jump,...quality[t.turn_id]};if(Object.values(values).some(v=>typeof v!=="boolean")){setErr("请先逐项判断三项对话表现");return;}setPending(t.turn_id);try{await reviewConversation(t.turn_id,values.answered as boolean,values.repeated_followup as boolean,values.topic_jump as boolean);await search();}catch(e){setErr(e instanceof Error?e.message:String(e));}finally{setPending("");}}}>保存对话审核</button>
                      </div>}
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
          {accessLog && <p className="panel panel-padding mt-4 text-xs" role="status">{accessLog}</p>}
        </>
      )}
      <footer className="page-footer"><span><Icon name="shield" size={12} /> 默认不播放录音，未授权片段不可播放</span><span>Internal QA</span></footer>
    </main>
  );
}
