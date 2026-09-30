"use client";

import { useEffect, useState } from "react";
import { getProgress } from "@/lib/api";
import type { Progress } from "@/lib/types";
import Icon, { type IconName } from "@/components/Icon";

const METRICS: { key: keyof Progress; label: string; icon: IconName; percent?: boolean }[] = [
  { key: "completed_count", label: "完成练习数", icon: "chat" },
  { key: "independent_rate", label: "独立完成率", icon: "target", percent: true },
  { key: "help_usage_rate", label: "帮助使用率", icon: "help", percent: true },
  { key: "due_retest_done_rate", label: "到期复练完成率", icon: "check", percent: true },
  { key: "mastered_count", label: "已掌握项目数", icon: "shield" },
  { key: "voice_evaluable_rate", label: "语音可评估率", icon: "mic", percent: true },
  { key: "drill_completed_count", label: "脱稿完成数", icon: "book" },
  { key: "transfer_completed_count", label: "迁移成功数", icon: "spark" },
  { key: "text_completed_count", label: "文字完成数", icon: "chat" },
];

export default function ProgressPage() {
  const [p, setP] = useState<Progress | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    getProgress()
      .then((d) => { if (active) setP(d); })
      .catch((e) => { if (active) setErr(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  function fmt(v: number, percent?: boolean) {
    return percent ? `${Math.round(v * 100)}%` : String(v);
  }

  return (
    <main className="page">
      <div className="topline"><span className="topline-title"><Icon name="spark" size={15} /> 我的学习空间 / 进步中心</span></div>
      <div className="eyebrow">YOUR PROGRESS</div>
      <h1 className="page-title">看得见的进步，练出来的底气。</h1>
      <p className="page-description">用事实说话：这里只展示有据可查的指标，不做伪精确的分数。</p>
      {err && <div className="error-banner" role="alert">{err}</div>}
      {loading ? (
        <div role="status" aria-label="加载进步数据"><div className="skeleton" /><div className="skeleton" /></div>
      ) : p ? (
        <>
          <div className="stat-grid">
            {METRICS.map((m) => (
              <div key={m.key} className="panel stat-card">
                <span className="icon-tile"><Icon name={m.icon} /></span>
                <div><strong>{fmt(p[m.key] as number, m.percent)}</strong><p>{m.label}</p></div>
              </div>
            ))}
          </div>
          <p className="text-xs muted mt-4">能力分（发音 / 语法 / 表达 / 任务完成）在校准前不展示；删除历史会同步重算。</p>
          {p.samples && <p className="text-xs muted mt-2">样本：可评估子目标 {p.samples.evaluable_subgoals}、语音回合 {p.samples.voice_turns}、到期项目 {p.samples.due_items}。</p>}
          {p.trend && p.trend.length > 0 && (
            <section className="panel mt-4"><div className="section-head mt-0"><h3><Icon name="target" size={16} /> 最近 7 天独立完成率</h3></div>
              <div className="flex flex-wrap gap-2">{p.trend.map((t) => <span key={t.date} className="badge badge-neutral">{t.date.slice(5)}：{Math.round(t.independent_rate * 100)}%（{t.evaluable}）</span>)}</div>
            </section>
          )}
          {p.mastered_list && p.mastered_list.length > 0 && (
            <section className="panel mt-4"><div className="section-head mt-0"><h3><Icon name="shield" size={16} /> 已掌握项目</h3></div>
              <ul className="text-sm">{p.mastered_list.map((m) => <li key={m.id} className="py-1">{m.suggestion_sentence || m.target_ability}</li>)}</ul>
            </section>
          )}
        </>
      ) : null}
      <footer className="page-footer"><span><Icon name="shield" size={12} /> 仅记录事实型指标，不做排名</span><span>Small steps. Real conversations.</span></footer>
    </main>
  );
}
