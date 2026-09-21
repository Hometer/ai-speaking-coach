"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { clearInviteCode, createSession, hasInviteCode, listSessions, setInviteCode } from "@/lib/api";
import Icon from "@/components/Icon";
import styles from "./page.module.css";

const openings: Record<string, string> = {
  beginner: "Tell me one simple thing about yourself, like your name and what you do.",
  basic: "Tell me about one experience you are proud of.",
  intermediate: "Walk me through one achievement and how you contributed to it.",
};
const levels = [{ value: "beginner", label: "入门", note: "从简单句开始" }, { value: "basic", label: "基础", note: "尝试完整表达" }, { value: "intermediate", label: "中级", note: "丰富细节与观点" }];

export default function Home() {
  const router = useRouter();
  const [invite, setInvite] = useState("");
  const [loggedIn, setLoggedIn] = useState<boolean | null>(null);
  const [level, setLevel] = useState("basic");
  const [difficulty, setDifficulty] = useState("standard");
  const [duration, setDuration] = useState(5);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    let mounted = true;
    Promise.resolve().then(() => { if (mounted) setLoggedIn(hasInviteCode()); });
    return () => { mounted = false; };
  }, []);

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
      const s = await createSession(level, difficulty, duration);
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
        <div><div className="eyebrow">MAKE YOURSELF HEARD</div><h1 className="page-title">今天，也给自己一点开口的时间。</h1><p className="page-description">从一段真实的对话开始，把想说的话，慢慢说得更好。</p></div>
        <span className={styles.greetingIcon} aria-hidden="true"><Icon name="leaf" size={30} /></span>
      </div>

      <div className={styles.workspace}>
        <div className={styles.mainColumn}>
          <section className={styles.hero} aria-labelledby="scene-title">
            <div className={styles.heroCopy}>
              <span className={styles.heroTag}><span /> 本次练习场景</span>
              <h2 id="scene-title">让面试官，<br />听见你的闪光点。</h2>
              <p>和 AI 教练练一段英文自我介绍，<br />从你的经历出发，自然地接住追问。</p>
              <div className={styles.heroMeta}><span><Icon name="clock" size={14} /> {duration} 分钟</span><span><Icon name="chat" size={14} /> 语音 / 文字</span></div>
            </div>
            <div className={styles.voiceArt} aria-hidden="true"><div className={styles.orbit} /><div className={styles.orbitInner} /><div className={styles.micDisc}><Icon name="mic" size={52} /></div><span className={styles.floatBubble}>Hello, nice to meet you.</span><div className={styles.wave}>{[12,22,34,19,44,31,17,26,13].map((h,i) => <i key={i} style={{height:h}} />)}</div><span className={styles.artStar}>✦</span></div>
          </section>

          <section className={styles.goalPanel}>
            <div className={styles.goalIcon}><Icon name="target" size={23} /></div>
            <div><h3>一个小目标，就从这里开始</h3><p>介绍一项自己的经历，并回答一个相关追问。</p></div>
            <span className={styles.goalNumber}>01</span>
          </section>

          <section className="panel panel-padding">
            <div className={styles.questionHeader}><span className="badge"><Icon name="headphones" size={13} /> 开场示例</span><span>先想一想，再开口</span></div>
            <p className={styles.question} lang="en">“{openings[level]}”</p>
            <p className={styles.questionNote}>实际问题会在开始练习后显示，不必准备完美答案。</p>
            <details className={styles.hint}><summary><Icon name="help" size={15} /> 不知道从哪儿说起？</summary><p>试着用三句话说明：发生了什么、你做了什么、学到了什么。只使用自己的真实经历，卡住时也可以向教练求助。</p></details>
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
              <fieldset><legend className="field-label">我的英语水平</legend><div className={styles.levels}>{levels.map(option => <button key={option.value} className={level === option.value ? styles.selected : ""} aria-pressed={level === option.value} onClick={() => setLevel(option.value)} disabled={loading}><span>{option.label}</span><small>{option.note}</small></button>)}</div></fieldset>
              <fieldset><legend className="field-label">这次想练的难度</legend><div className={styles.segments}>{[{value:"simple",label:"简单",note:"轻松开口"},{value:"standard",label:"标准",note:"多一点挑战"}].map(option => <button key={option.value} className={difficulty === option.value ? styles.selected : ""} aria-pressed={difficulty === option.value} onClick={() => setDifficulty(option.value)} disabled={loading}>{option.label}<small>{option.note}</small></button>)}</div></fieldset>
              <fieldset><legend className="field-label">给自己几分钟？</legend><div className={styles.durations}>{[5,10,15].map(value => <button key={value} className={duration === value ? styles.selected : ""} aria-pressed={duration === value} onClick={() => setDuration(value)} disabled={loading}>{value}<small> 分钟</small>{value === 5 && <span>推荐</span>}</button>)}</div></fieldset>
              <button className={`btn btn-primary ${styles.start}`} onClick={start} disabled={loading}><Icon name="mic" size={18} />{loading ? "正在准备练习…" : "开始练习"}<Icon name="arrow" size={18} /></button>
              <p className={styles.micNote}><Icon name="shield" size={12} /> 开始后，点击录音才会开启麦克风</p>
              <button className={`text-link ${styles.logout}`} onClick={logout} disabled={loading}><Icon name="logout" size={13} /> 退出 / 切换邀请凭证</button>
            </div>
          )}
          {err && <p role="alert" className="error-banner">{err}</p>}
        </section>
      </div>

      <div className="section-head"><h2>让每一次练习，都有收获</h2><span>练习 · 回顾 · 再尝试</span></div>
      <div className={styles.featureGrid}>
        <div className={styles.feature}><span className={styles.featureIndex}>01</span><Icon name="chat" /><h3>放心说，卡住也没关系</h3><p>没听清、没听懂、不会组织？<br />练习中随时向教练求助。</p></div>
        <Link href="/history" className={styles.feature}><span className={styles.featureIndex}>02</span><Icon name="history" /><h3>回头看，找到下一次的进步</h3><p>重看对话和有依据的表达建议，<br />再练一次，让表达更自然。</p><Icon className={styles.featureArrow} name="arrow" size={17} /></Link>
        <Link href="/memory" className={styles.feature}><span className={styles.featureIndex}>03</span><Icon name="book" /><h3>是否记住，由你决定</h3><p>学习记忆默认关闭，<br />你可以查看、关闭或删除。</p><Icon className={styles.featureArrow} name="arrow" size={17} /></Link>
      </div>
      <footer className="page-footer"><span><Icon name="shield" size={12} /> 仅用于口语练习，不代表真实面试标准</span><span>Small steps. Real conversations.</span></footer>
    </main>
  );
}
