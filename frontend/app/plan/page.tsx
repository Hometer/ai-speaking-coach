"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { createPlan, getPlan, pausePlan, restartPlan, resumePlan, skipPlanDay } from "@/lib/api";
import type { TrainingPlan } from "@/lib/types";
import Icon from "@/components/Icon";

const GOALS = [
  { value: "job_prep", label: "求职准备" },
  { value: "workplace", label: "职场表达" },
  { value: "travel", label: "旅行准备" },
  { value: "daily", label: "日常开口" },
];

export default function PlanPage() {
  const [plan, setPlan] = useState<TrainingPlan | null>(null);
  const [goal, setGoal] = useState("job_prep");
  const [availableMinutes, setAvailableMinutes] = useState(5);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [pending, setPending] = useState("");

  useEffect(() => {
    let active = true;
    getPlan()
      .then((p) => { if (active) { setPlan(p); if (p) { setAvailableMinutes(p.available_minutes || 5); setGoal(p.goal_type); } } })
      .catch((e) => { if (active) setErr(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  async function create() {
    setPending("create"); setErr("");
    try { setPlan(await createPlan(goal, availableMinutes)); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setPending(""); }
  }

  async function skip(day: number) {
    setPending(String(day)); setErr("");
    try { setPlan(await skipPlanDay(day)); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setPending(""); }
  }

  async function restart() {
    setPending("restart"); setErr("");
    try { setPlan(await restartPlan()); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setPending(""); }
  }

  async function togglePause() {
    if (!plan) return;
    setPending("pause"); setErr("");
    try { setPlan(plan.status === "paused" ? await resumePlan() : await pausePlan()); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setPending(""); }
  }

  return (
    <main className="page">
      <div className="topline"><span className="topline-title"><Icon name="spark" size={15} /> 我的学习空间 / 七日计划</span><Link href="/" className="text-link">开始新的练习 <Icon name="arrow" size={14} /></Link></div>
      <div className="eyebrow">SEVEN-DAY PLAN</div>
      <h1 className="page-title">七天，一点点把开口变顺。</h1>
      <p className="page-description">每天一个主场景 + 一个可选复练，按你的目标排好，不催不罚。</p>
      {err && <div className="error-banner" role="alert">{err}</div>}

      <div className="panel panel-padding mb-5">
        <span className="field-label">这次想练的目标</span>
        <div className="flex flex-wrap gap-3 mt-3">
          {GOALS.map((g) => (
            <button key={g.value} className={`btn ${goal === g.value ? "btn-primary" : "btn-ghost"} btn-small`} onClick={() => setGoal(g.value)} disabled={!!pending}>{g.label}</button>
          ))}
        </div>
        <label className="field-label mt-4">每天可用时间 <select className="field field-small" value={availableMinutes} onChange={e => setAvailableMinutes(Number(e.target.value))} disabled={!!pending}><option value={5}>5 分钟</option><option value={10}>10 分钟</option><option value={15}>15 分钟</option></select></label>
        <div className="flex gap-3 mt-4">
          <button className="btn btn-primary" onClick={create} disabled={!!pending}>{pending === "create" ? "生成中…" : "生成 / 更新计划"}</button>
          {plan && <button className="btn btn-ghost" onClick={togglePause} disabled={!!pending}>{plan.status === "paused" ? "恢复计划" : "暂停计划"}</button>}
          {plan && <button className="btn btn-ghost" onClick={restart} disabled={!!pending}>重新开始</button>}
        </div>
      </div>

      {loading ? (
        <div role="status" aria-label="加载计划"><div className="skeleton" /><div className="skeleton" /></div>
      ) : plan && plan.days.length > 0 ? (
        <div className="history-list">
          {plan.days.map((d) => (
            <article key={d.day} className="panel history-row">
              <span className="icon-tile"><Icon name="clock" /></span>
              <div className="history-info">
                <h3>第 {d.day} 天 · {d.scene_label}</h3>
                <div className="history-meta">
                  <span>{d.scheduled_date}</span>
                  <span>{d.status === "skipped" ? "已跳过" : d.status === "completed" ? "已完成" : "待练"}</span>
                  {d.retest_item_id ? <span>含一项到期复练</span> : <span>可选复练：无到期项</span>}
                  {d.adjustment_reason && <span>{d.adjustment_reason}</span>}
                </div>
              </div>
              <span className={`badge ${d.status !== "completed" ? "badge-neutral" : ""}`}>{d.status === "skipped" ? "已跳过" : d.status === "completed" ? "已完成" : "待练"}</span>
              <div className="row-actions">
                {plan.status === "active" && d.status === "pending" && d.scheduled_date === new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Shanghai" }) ? <Link className="btn btn-secondary btn-small" href={`/?scene=${d.scene_id}&plan_id=${plan.id}&plan_day=${d.day}`}>练这一场景 <Icon name="arrow" size={13} /></Link> : <span className="text-xs muted">{d.status === "pending" ? "到计划日期可练" : "本日已处理"}</span>}
                {d.status !== "skipped" && <button onClick={() => skip(d.day)} disabled={!!pending} className="btn btn-ghost btn-small">跳过</button>}
              </div>
            </article>
          ))}
        </div>
      ) : (
        <div className="panel empty-state"><span className="icon-tile"><Icon name="spark" size={30} /></span><h2>先选一个目标</h2><p>选好目标，点「生成计划」，七天安排就会出现在这里。</p></div>
      )}

      <footer className="page-footer"><span><Icon name="shield" size={12} /> 计划只组合已审核场景，不生成未审核内容</span><span>Small steps. Real conversations.</span></footer>
    </main>
  );
}
