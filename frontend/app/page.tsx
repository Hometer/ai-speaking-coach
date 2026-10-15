"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  clearInviteCode,
  createSession,
  dueToday,
  getInviteCode,
  getPlan,
  getScenario,
  hasInviteCode,
  listQueue,
  listScenarios,
  listSessions,
  recordDeviceCheck,
  setInviteCode,
  selectScene,
} from "@/lib/api";
import type { Scenario } from "@/lib/types";
import Icon from "@/components/Icon";
import styles from "./page.module.css";

const levels = [
  { value: "beginner", label: "入门", note: "从简单句开始" },
  { value: "basic", label: "基础", note: "尝试完整表达" },
  { value: "intermediate", label: "中级", note: "丰富细节与观点" },
];

const seriesOrder = [
  { key: "career", label: "职场与求职" },
  { key: "travel", label: "旅行与生活" },
  { key: "daily", label: "日常交流" },
];

type DeviceItem = { key: string; label: string; ok: boolean; hint: string };

async function checkDevice(): Promise<{ ok: boolean; summary: string; items: DeviceItem[] }> {
  const items: DeviceItem[] = [];
  // 1. 麦克风权限 + 2. 一秒短录听回，用户确认实际输入
  let micOk = false;
  let inputOk = false;
  if (typeof navigator !== "undefined" && navigator.mediaDevices?.getUserMedia) {
    let stream: MediaStream | null = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      micOk = true;
      if (stream.getAudioTracks().length > 0 && typeof MediaRecorder !== "undefined") {
        const chunks: Blob[] = [];
        const recorder = new MediaRecorder(stream);
        const blob = await new Promise<Blob>((resolve, reject) => {
          recorder.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
          recorder.onerror = () => reject(new Error("录音失败"));
          recorder.onstop = () => resolve(new Blob(chunks, { type: recorder.mimeType || "audio/webm" }));
          recorder.start();
          window.setTimeout(() => { if (recorder.state === "recording") recorder.stop(); }, 1200);
        });
        if (blob.size > 100) {
          const url = URL.createObjectURL(blob);
          try {
            const audio = new Audio(url);
            await audio.play();
            await new Promise<void>(resolve => { audio.onended = () => resolve(); audio.onerror = () => resolve(); window.setTimeout(resolve, 5000); });
            inputOk = window.confirm("刚才播放的是一段短录音。你能听到自己刚说的话吗？");
          } finally { URL.revokeObjectURL(url); }
        }
      }
    } catch {
      inputOk = false;
    } finally {
      stream?.getTracks().forEach((t) => t.stop());
    }
  }
  items.push({ key: "mic", label: "麦克风权限", ok: micOk, hint: "在浏览器地址栏左侧授权本站使用麦克风" });
  items.push({ key: "input", label: "录音输入", ok: inputOk, hint: "检查系统麦克风是否可用、未被占用" });
  // 3. 音频播放：AudioContext 成功运行后仍请用户确认听到声音。
  let playOk = false;
  try {
    const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (Ctx) {
      const ctx = new Ctx();
      await ctx.resume();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      gain.gain.value = 0.06;
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.35);
      osc.onended = () => { void ctx.close(); };
      await new Promise(resolve => window.setTimeout(resolve, 450));
      playOk = ctx.state !== "suspended" && window.confirm("刚才是否听到了短提示音？");
    }
  } catch {
    playOk = false;
  }
  items.push({ key: "play", label: "音频播放", ok: playOk, hint: "检查扬声器音量与设备连接" });
  // 4. 网络连通
  let netOk = false;
  try {
    const r = await fetch("/api/v1/scenarios", { headers: { "X-Invite-Code": getInviteCode() } });
    netOk = r.ok;
  } catch {
    netOk = false;
  }
  items.push({ key: "net", label: "网络连通", ok: netOk, hint: "检查网络连接后重试" });
  const problems = items.filter((i) => !i.ok).map((i) => i.label);
  return { ok: items.every((i) => i.ok), summary: problems.join("、") || "ok", items };
}

export default function Home() {
  const router = useRouter();
  const [invite, setInvite] = useState("");
  const [loggedIn, setLoggedIn] = useState<boolean | null>(null);
  const [level, setLevel] = useState("basic");
  const [difficulty, setDifficulty] = useState("standard");
  const [duration, setDuration] = useState(5);
  const [scenes, setScenes] = useState<Scenario[]>([]);
  const [sceneId, setSceneId] = useState(() => {
    if (typeof window !== "undefined") {
      return new URLSearchParams(window.location.search).get("scene") || "job_interview";
    }
    return "job_interview";
  });
  const [planEntry, setPlanEntry] = useState<{ planId: number; planDay: number; sceneId: string } | null>(() => {
    if (typeof window === "undefined") return null;
    const p = new URLSearchParams(window.location.search);
    const planId = Number(p.get("plan_id"));
    const planDay = Number(p.get("plan_day"));
    const sceneId = p.get("scene") || "";
    return planId > 0 && planDay >= 1 && planDay <= 7 && sceneId ? { planId, planDay, sceneId } : null;
  });
  const [scene, setScene] = useState<Scenario | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [dueCount, setDueCount] = useState<number | null>(null);
  const [queueCount, setQueueCount] = useState<number | null>(null);
  const [planToday, setPlanToday] = useState<{ day: number; label: string } | null>(null);
  const [deviceItems, setDeviceItems] = useState<DeviceItem[]>([]);

  useEffect(() => {
    let mounted = true;
    Promise.resolve().then(() => { if (mounted) setLoggedIn(hasInviteCode()); });
    return () => { mounted = false; };
  }, []);

  useEffect(() => {
    if (!loggedIn) return;
    listScenarios()
      .then((list) => {
        setScenes(list);
        if (list.length > 0 && !list.some((s) => s.scene_id === sceneId)) {
          setSceneId(list[0].scene_id);
        }
      })
      .catch(() => setScenes([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loggedIn]);

  useEffect(() => {
    if (!loggedIn) return;
    getScenario(sceneId)
      .then(setScene)
      .catch(() => setScene(null));
  }, [loggedIn, sceneId]);

  useEffect(()=>{
    if(loggedIn && scene?.scene_id===sceneId) void selectScene(sceneId).catch(()=>{});
  },[loggedIn,sceneId,scene]);

  useEffect(() => {
    if (!loggedIn) return;
    let active = true;
    dueToday()
      .then((d) => { if (active) setDueCount(d.count); })
      .catch(() => { if (active) setDueCount(null); });
    listQueue()
      .then((q) => { if (active) setQueueCount(q.filter((i) => i.status === "pending" || i.status === "awaiting_retest").length); })
      .catch(() => { if (active) setQueueCount(null); });
    getPlan()
      .then((p) => {
        if (!active) return;
        if (!p || p.days.length === 0) { setPlanToday(null); return; }
        const today = p.days.find((d) => d.status !== "skipped") ?? p.days[0];
        setPlanToday({ day: today.day, label: today.scene_label });
      })
      .catch(() => { if (active) setPlanToday(null); });
    return () => { active = false; };
  }, [loggedIn]);

  async function login() {
    if (!invite.trim()) { setErr("请输入你的邀请凭证"); return; }
    setLoading(true);
    setErr("");
    setInviteCode(invite);
    try {
      await listSessions();
      setLoggedIn(true);
      setInvite("");
    } catch (e) {
      clearInviteCode();
      setErr(e instanceof Error ? e.message : "暂时无法进入，请重试。");
    } finally { setLoading(false); }
  }

  function logout() {
    clearInviteCode();
    setInvite("");
    setLoggedIn(false);
    setErr("");
  }

  async function start() {
    if (loading) return;
    setLoading(true);
    setErr("");
    try {
      // 设备预检（失败不阻断，可转文字练习）
      const device = await checkDevice();
      setDeviceItems(device.items);
      const check = await recordDeviceCheck(device.ok ? "ok" : "degraded", device.summary).catch(() => null);
      const selectionId = await selectScene(sceneId).catch(()=>undefined);
      if (!device.ok) {
        setErr(`设备提示：${device.summary}。你仍可开始练习，语音回答可能受限，可用文字替代。`);
      }
      const s = await createSession(level, difficulty, duration, sceneId, planEntry && planEntry.sceneId === sceneId ? planEntry : undefined, {selectionId,deviceCheckId:check?.device_check_id});
      router.push(`/practice/${s.session_id}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setLoading(false);
    }
  }

  return (
    <main className="page">
      <div className="topline"><span className="topline-title"><Icon name="home" size={15} /> 我的学习空间 <span className="muted">/</span> 开始练习</span><span className="subtle-tag"><Icon name="spark" size={12} /> 英语口语 · 内测版</span></div>
      <div className={styles.greeting}>
        <div><div className="eyebrow">MAKE YOURSELF HEARD</div><h1 className="page-title">今天，也给自己一点开口的时间。</h1><p className="page-description">选一个真实会遇到的场景，把想说的话，慢慢说得更好。</p></div>
        <span className={styles.greetingIcon} aria-hidden="true"><Icon name="leaf" size={30} /></span>
      </div>

      <div className={styles.workspace}>
        <div className={styles.mainColumn}>
          <section className={styles.hero} aria-labelledby="scene-title">
            <div className={styles.heroCopy}>
              <span className={styles.heroTag}><span /> 本次练习场景{scene ? ` · ${scene.series_label}` : ""}</span>
              <h2 id="scene-title">{scene ? scene.label : "选一个场景"}</h2>
              <p>{scene ? `${scene.goal_zh}。你是${scene.user_role}，对面是${scene.coach_role}。` : "从下方选择一个真实场景开始。"}</p>
              <div className={styles.heroMeta}><span><Icon name="clock" size={14} /> {duration} 分钟</span><span><Icon name="chat" size={14} /> 语音 / 文字</span></div>
            </div>
            <div className={styles.voiceArt} aria-hidden="true"><div className={styles.orbit} /><div className={styles.orbitInner} /><div className={styles.micDisc}><Icon name="mic" size={52} /></div><span className={styles.floatBubble}>Hello, nice to meet you.</span><div className={styles.wave}>{[12,22,34,19,44,31,17,26,13].map((h,i) => <i key={i} style={{height:h}} />)}</div><span className={styles.artStar}>✦</span></div>
          </section>

          <section className={styles.goalPanel}>
            <div className={styles.goalIcon}><Icon name="target" size={23} /></div>
            <div><h3>一个小目标，就从这里开始</h3><p>{scene ? scene.goal_zh : "选择场景后，这里会显示本次的小目标。"}</p></div>
            <span className={styles.goalNumber}>01</span>
          </section>

          <section className="panel panel-padding">
            <div className={styles.questionHeader}><span className="badge"><Icon name="headphones" size={13} /> 开场示例</span><span>先想一想，再开口</span></div>
            <p className={styles.question} lang="en">“{scene?.openings?.[level] ?? "选择场景后显示开场问题"}”</p>
            <p className={styles.questionNote}>实际问题会在开始练习后显示，不必准备完美答案。</p>
            <details className={styles.hint}><summary><Icon name="help" size={15} /> 不知道从哪儿说起？</summary><p>试着先说清楚发生了什么、你做了什么、结果怎么样。只使用自己的真实情况，卡住时也可以向教练求助。</p></details>
          </section>
        </div>

        <section className={`panel ${styles.setup}`} aria-labelledby="setup-title">
          <div className={styles.setupHeading}><span className="icon-tile"><Icon name={loggedIn ? "mic" : "shield"} size={22} /></span><div><h2 id="setup-title">{loggedIn ? "准备好，就开始" : "欢迎来到练习空间"}</h2><p>{loggedIn ? "按你的节奏，轻松练一段" : "使用邀请凭证，开启你的练习"}</p></div></div>
          {loggedIn === null ? <div role="status" aria-label="加载设置"><div className="skeleton" /><div className="skeleton" /></div> : !loggedIn ? (
            <form className={styles.login} onSubmit={e => { e.preventDefault(); if (!loading) void login(); }}>
              <label htmlFor="invite" className="field-label">邀请凭证</label><input id="invite" type="password" className="field" autoComplete="off" placeholder="输入你的邀请凭证" value={invite} onChange={e => setInvite(e.target.value)} required />
              <p className={styles.loginNote}>当前面向受邀成人用户开放。请使用分配给你的独立凭证。</p>
              <button className="btn btn-primary w-full" disabled={loading} type="submit">{loading ? "正在验证…" : "进入练习空间"}<Icon name="arrow" size={17} /></button>
              <div className={styles.loginPromise}><Icon name="shield" size={18} /><p>学习记忆默认关闭。<br />你的每一次练习，由你自己掌控。</p></div>
            </form>
          ) : (
            <div className={styles.settings}>
              <fieldset><legend className="field-label">这次想练的场景</legend><div className={styles.sceneGroups}>{seriesOrder.map(series => { const items = scenes.filter(s => s.series === series.key); if (items.length === 0) return null; return <div key={series.key} className={styles.sceneGroup}><span className={styles.sceneSeries}>{series.label}</span><div className={styles.sceneChips}>{items.map(s => <button key={s.scene_id} className={sceneId === s.scene_id ? styles.selected : ""} aria-pressed={sceneId === s.scene_id} onClick={() => { setSceneId(s.scene_id); if (planEntry?.sceneId !== s.scene_id) setPlanEntry(null); }} disabled={loading}>{s.label}</button>)}</div></div>; })}</div></fieldset>
              <fieldset><legend className="field-label">我的英语水平</legend><div className={styles.levels}>{levels.map(option => <button key={option.value} className={level === option.value ? styles.selected : ""} aria-pressed={level === option.value} onClick={() => setLevel(option.value)} disabled={loading}><span>{option.label}</span><small>{option.note}</small></button>)}</div></fieldset>
              <fieldset><legend className="field-label">这次想练的难度</legend><div className={styles.segments}>{[{value:"simple",label:"简单",note:"轻松开口"},{value:"standard",label:"标准",note:"多一点挑战"}].map(option => <button key={option.value} className={difficulty === option.value ? styles.selected : ""} aria-pressed={difficulty === option.value} onClick={() => setDifficulty(option.value)} disabled={loading}>{option.label}<small>{option.note}</small></button>)}</div></fieldset>
              <fieldset><legend className="field-label">给自己几分钟？</legend><div className={styles.durations}>{[5,10,15].map(value => <button key={value} className={duration === value ? styles.selected : ""} aria-pressed={duration === value} onClick={() => setDuration(value)} disabled={loading}>{value}<small> 分钟</small>{value === 5 && <span>推荐</span>}</button>)}</div></fieldset>
              <button className={`btn btn-primary ${styles.start}`} onClick={start} disabled={loading}><Icon name="mic" size={18} />{loading ? "正在准备练习…" : "开始练习"}<Icon name="arrow" size={18} /></button>
              {deviceItems.length > 0 && <div className={styles.deviceGrid}>{deviceItems.map(item => <span key={item.key} className={item.ok ? styles.deviceOk : styles.deviceBad} title={item.ok ? "正常" : item.hint}><Icon name={item.ok ? "check" : "shield"} size={12} />{item.label}{!item.ok && <small>{item.hint}</small>}</span>)}</div>}
              <p className={styles.micNote}><Icon name="shield" size={12} /> 开始后，点击录音才会开启麦克风；语音不可用时可用文字</p>
              <button className={`text-link ${styles.logout}`} onClick={logout} disabled={loading}><Icon name="logout" size={13} /> 退出 / 切换邀请凭证</button>
            </div>
          )}
          {err && <p role="alert" className="error-banner">{err}</p>}
        </section>
      </div>

      {loggedIn && (
        <section className={styles.todayZone} aria-labelledby="today-title">
          <div className="section-head mt-0">
            <div><div className="eyebrow">TODAY’S TASKS</div><h2 id="today-title">今日任务</h2></div>
            <span>一次只做一件事</span>
          </div>
          <div className={styles.todayGrid}>
            <Link href="/daily-review" className={styles.todayCard}>
              <span className={styles.todayIcon}><Icon name="target" size={19} /></span>
              <div className={styles.todayBody}><h3>今日复练</h3><p>{dueCount === null ? "正在读取…" : dueCount > 0 ? `${dueCount} 条到期，约 3~5 分钟` : "今天暂无到期内容"}</p></div>
              <span className={`badge ${dueCount ? "" : "badge-neutral"}`}>{dueCount ? `${dueCount} 条待练` : "已完成"}</span>
              <Icon className={styles.todayArrow} name="chevron" size={15} />
            </Link>
            <Link href="/plan" className={styles.todayCard}>
              <span className={styles.todayIcon}><Icon name="clock" size={19} /></span>
              <div className={styles.todayBody}><h3>七日计划</h3><p>{planToday ? `第 ${planToday.day} 天 · ${planToday.label}` : "还没有计划，可先选一个目标"}</p></div>
              <span className="badge badge-neutral">{planToday ? `第 ${planToday.day} 天` : "待创建"}</span>
              <Icon className={styles.todayArrow} name="chevron" size={15} />
            </Link>
            <Link href="/queue" className={styles.todayCard}>
              <span className={styles.todayIcon}><Icon name="check" size={19} /></span>
              <div className={styles.todayBody}><h3>待复练</h3><p>{queueCount === null ? "正在读取…" : queueCount > 0 ? `${queueCount} 条待练/待复测` : "队列是空的，先积累想再练的表达"}</p></div>
              <span className={`badge ${queueCount ? "" : "badge-neutral"}`}>{queueCount ? `${queueCount} 条` : "无"}</span>
              <Icon className={styles.todayArrow} name="chevron" size={15} />
            </Link>
          </div>
        </section>
      )}

      <div className="section-head"><h2>让每一次练习，都有收获</h2><span>练习 · 回顾 · 再尝试</span></div>
      <div className={styles.featureGrid}>
        <div className={styles.feature}><span className={styles.featureIndex}>01</span><Icon name="chat" /><h3>放心说，卡住也没关系</h3><p>没听清、没听懂、不会组织？<br />练习中随时向教练求助。</p></div>
        <Link href="/history" className={styles.feature}><span className={styles.featureIndex}>02</span><Icon name="history" /><h3>回头看，找到下一次的进步</h3><p>重看对话和有依据的表达建议，<br />再练一次，让表达更自然。</p><Icon className={styles.featureArrow} name="arrow" size={17} /></Link>
        <Link href="/memory" className={styles.feature}><span className={styles.featureIndex}>03</span><Icon name="book" /><h3>是否记住，由你决定</h3><p>学习记忆默认关闭，<br />你可以查看、关闭或删除。</p><Icon className={styles.featureArrow} name="arrow" size={17} /></Link>
      </div>
      <footer className="page-footer"><span><Icon name="shield" size={12} /> 仅用于口语练习，不代表真实场景标准</span><span>Small steps. Real conversations.</span></footer>
    </main>
  );
}
