"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { createPracticeSentence, disputeReview, drillAction, drillAttempt, endSession, getInviteCode, getSession, pauseSession, resumeSession, requestHelp, retryTurnAudio, reviseTurn, skipPractice, submitTurn } from "@/lib/api";
import type { DrillView, TurnDone } from "@/lib/types";
import AudioPlayer from "@/components/AudioPlayer";
import Icon from "@/components/Icon";
import styles from "./page.module.css";

interface Msg {
  who: "user" | "coach";
  text: string;
  audioUrl?: string;
  turnId?: string;
}

const STATUS_CN: Record<string, string> = {
  WAITING_INPUT: "等待回答",
  RECHECK: "复测中",
  PRACTICE: "跟读中",
  ENDED: "已结束",
  PAUSED: "已暂停",
};

export default function PracticePage() {
  const params = useParams<{ sessionId: string }>();
  const sessionId = params.sessionId;
  const router = useRouter();

  const [goal, setGoal] = useState("");
  const [sceneLabel, setSceneLabel] = useState("");
  const [status, setStatus] = useState("");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [showHelp, setShowHelp] = useState(false);
  const [practiceSentence, setPracticeSentence] = useState("");
  const [practiceDemo, setPracticeDemo] = useState("");
  const [practiceResult, setPracticeResult] = useState("");
  const [recording, setRecording] = useState(false);
  const [audioRetryTurnId, setAudioRetryTurnId] = useState<string | null>(null);
  const [drill, setDrill] = useState<DrillView | null>(null);
  const [drillText, setDrillText] = useState("");
  const [recordMode, setRecordMode] = useState<"answer" | "practice" | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [review, setReview] = useState<Record<string, unknown> | null>(null);
  const [sourceInvalid, setSourceInvalid] = useState(false);
  const [sessionLoading, setSessionLoading] = useState(true);

  const recRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const cancelRef = useRef(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let active = true;
    getSession(sessionId)
      .then((d) => {
        if (!active) return;
        setGoal(d.goal_text);
        setSceneLabel(d.scene_label || "");
        setStatus(d.status);
        setReview(d.review);
        setSourceInvalid(!!d.source_invalid);
        setDrill(d.drill);
        const msgs: Msg[] = [];
        for (const t of d.turns) {
          if (t.input_type === "coach") {
            msgs.push({ who: "coach", text: t.reply_text, audioUrl: t.reply_audio_url });
            if (t.practice_sentence && d.status === "PRACTICE") {
              setPracticeSentence(t.practice_sentence);
              setPracticeDemo(t.reply_audio_url);
            }
          } else if (t.input_type === "practice") {
            msgs.push({ who: "user", text: `（跟读：${t.practice_sentence || "短句练习"}）` });
            if (t.reply_text) msgs.push({ who: "coach", text: t.reply_text, audioUrl: t.reply_audio_url });
          } else {
            msgs.push({ who: "user", text: t.revised_text || t.user_text || t.transcript || "（语音回答）", turnId: t.turn_id });
            if (t.reply_text) msgs.push({ who: "coach", text: t.reply_text, audioUrl: t.reply_audio_url });
            if (t.practice_sentence && d.status === "PRACTICE") { setPracticeSentence(t.practice_sentence); setPracticeDemo(t.reply_audio_url); }
          }
        }
        setMessages(msgs);
      })
      .catch((e) => { if (active) setErr(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (active) setSessionLoading(false); });
    return () => { active = false; };
  }, [sessionId]);

  useEffect(() => {
    if (messages.length > 1) bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages]);

  useEffect(() => () => {
    cancelRef.current = true;
    if (timerRef.current) clearInterval(timerRef.current);
    if (recRef.current) {
      recRef.current.onstop = null;
      if (recRef.current.state === "recording") recRef.current.stop();
    }
    streamRef.current?.getTracks().forEach(track => track.stop());
  }, []);

  function addMsg(who: Msg["who"], text: string, audioUrl?: string, turnId?: string) {
    setMessages((m) => [...m, { who, text, audioUrl, turnId }]);
  }

  function attachLastCoachAudio(audioUrl?: string) {
    setMessages((ms) => {
      const copy = [...ms];
      for (let i = copy.length - 1; i >= 0; i--) {
        if (copy[i].who === "coach") {
          copy[i] = { ...copy[i], audioUrl: audioUrl || copy[i].audioUrl };
          break;
        }
      }
      return copy;
    });
  }

  async function handleTurn(input: { text?: string; audio?: Blob }, typedText?: string) {
    setBusy(true);
    setErr("");
    setAudioRetryTurnId(null);
    let coachShown = false;
    try {
      if (typedText) { setText(""); addMsg("user", typedText); }
      const done: TurnDone = await submitTurn(sessionId, input, (text) => {
        coachShown = true;
        addMsg("coach", text); // chunk：教练文字生成后立即显示
      });
      if (!typedText) {
        if (done.transcript) addMsg("user", done.transcript, undefined, done.turn_id);
        else addMsg("user", "（语音回答）", undefined, done.turn_id);
      }
      if (!coachShown) {
        addMsg("coach", done.reply_text, done.reply_audio_url, done.turn_id);
      } else {
        attachLastCoachAudio(done.reply_audio_url); // done 到达后补充音频
      }
      if (done.audio_status === "failed") {
        setErr("语音暂不可用，可以阅读文字或重试语音。");
        setAudioRetryTurnId(done.turn_id);
      }
      setStatus(done.status);
      if (done.action_type === "suggest_practice" && done.practice_sentence) {
        setPracticeSentence(done.practice_sentence);
        setPracticeDemo(done.reply_audio_url);
        setPracticeResult("");
      } else {
        setPracticeSentence("");
        setPracticeDemo("");
      }
      if (done.status === "ENDED") {
        const detail = await getSession(sessionId);
        setReview(detail.review);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleAudioRetry() {
    if (!audioRetryTurnId || busy) return;
    setBusy(true);
    setErr("");
    try {
      const r = await retryTurnAudio(sessionId, audioRetryTurnId);
      attachLastCoachAudio(r.reply_audio_url);
      if (r.audio_status === "failed") setErr("语音仍不可用，可以阅读文字。");
      else { setErr(""); setAudioRetryTurnId(null); }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleDrillAttempt() {
    if (!drill || busy || !drillText.trim()) return;
    setBusy(true);
    setErr("");
    try {
      const r = await drillAttempt(sessionId, drillText.trim());
      setDrillText("");
      setDrill(r);
      if (r.completed) setStatus("ENDED");
      setErr(r.success ? (r.result === "independent" ? "脱稿成功，已记为本次独立表达。" : r.result === "transfer" ? "换题成功，已形成迁移证据。" : "说对了，进入下一级。") : "没对上，再试一次或降一级。");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleDrillAction(action: "skip" | "downgrade" | "end") {
    if (!drill || busy) return;
    setBusy(true);
    setErr("");
    try {
      const r = await drillAction(sessionId, action);
      setDrill(r);
      if (r.completed) setStatus("ENDED");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function sendText() {
    const t = text.trim();
    if (!t || busy || recording || status === "ENDED" || status === "PAUSED" || sessionLoading) return;
    handleTurn({ text: t }, t);
  }

  function clearTimer() {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }

  function timerText() {
    if (!recording) return "";
    if (elapsed >= 50) return `还剩 ${60 - elapsed} 秒`;
    return `已录 ${elapsed} 秒`;
  }

  async function toggleRecord(mode: "answer" | "practice") {
    if (busy || sessionLoading || status === "ENDED" || status === "PAUSED") return;
    if (recRef.current && recRef.current.state === "recording") {
      cancelRef.current = false;
      recRef.current.stop();
      return;
    }
    try {
      document.querySelectorAll("audio").forEach(audio => audio.pause());
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      chunksRef.current = [];
      cancelRef.current = false;
      setElapsed(0);
      const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : "";
      const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      recRef.current = rec;
      rec.ondataavailable = (e) => {
        if (e.data.size) chunksRef.current.push(e.data);
      };
      rec.onstop = () => {
        clearTimer();
        stream.getTracks().forEach((t) => t.stop());
        setRecording(false);
        setRecordMode(null);
        setElapsed(0);
        if (cancelRef.current) {
          cancelRef.current = false;
          return;
        }
        const blob = new Blob(chunksRef.current, { type: rec.mimeType || "audio/webm" });
        if (mode === "answer") handleTurn({ audio: blob });
        else submitPractice(blob);
      };
      rec.start();
      setRecording(true);
      setRecordMode(mode);
      timerRef.current = setInterval(() => {
        setElapsed((prev) => {
          const next = prev + 1;
          if (next >= 60 && recRef.current && recRef.current.state === "recording") {
            cancelRef.current = false;
            recRef.current.stop();
          }
          return next;
        });
      }, 1000);
    } catch {
      setErr("无法获取麦克风，可用文字输入。");
    }
  }

  function cancelRecord() {
    if (recRef.current && recRef.current.state === "recording") {
      cancelRef.current = true;
      recRef.current.stop();
    }
  }

  async function submitPractice(blob: Blob) {
    setBusy(true);
    setErr("");
    try {
      const fd = new FormData();
      fd.append("audio", blob, "practice");
      fd.append("sentence", practiceSentence);
      const res = await fetch(`/api/v1/sessions/${sessionId}/practice`, {
        method: "POST",
        headers: { "X-Invite-Code": getInviteCode() },
        body: fd,
      });
      const data = await res.json();
      if (!res.ok) {
        setErr(data?.error?.message ?? `HTTP ${res.status}`);
        return;
      }
      const score = typeof data.eval?.score === "number" ? data.eval.score : null;
      const isMock = !!data.eval?.mock;
      setPracticeResult(score === null ? "本次无法评估" : `评测得分 ${score}${isMock ? "（示例）" : "（实测）"}`);
      addMsg("coach", data.recheck_reply_text, data.recheck_audio_url);
      setStatus(data.status);
      setPracticeSentence("");
      setPracticeDemo("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleSkipPractice() {
    setBusy(true);
    setErr("");
    try {
      const r = await skipPractice(sessionId);
      addMsg("coach", r.reply_text, r.reply_audio_url);
      setStatus(r.status);
      setPracticeSentence("");
      setPracticeDemo("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleHelp(type: string) {
    setBusy(true);
    setErr("");
    setShowHelp(false);
    try {
      let sourceText: string | undefined;
      if (type === "translate") {
        const src = window.prompt("请输入你想表达的中文：", "");
        if (!src || !src.trim()) {
          setErr("已取消，未请求翻译。");
          return;
        }
        sourceText = src.trim();
      }
      const r = await requestHelp(sessionId, type, sourceText);
      if (type === "repeat") {
        // 重播已有音频，不追加新消息
        if (r.reply_audio_url) {
          const a = new Audio(r.reply_audio_url);
          a.play().catch(() => setErr("音频播放失败，请重试。"));
        }
      } else {
        addMsg("coach", r.reply_text, r.reply_audio_url);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleRevise(turnId: string) {
    const newText = window.prompt("修改转写内容：", "");
    if (!newText || !newText.trim()) return;
    try {
      await reviseTurn(sessionId, turnId, newText.trim());
      setErr("已提交修订，该回合评分已作废。");
      const d = await getSession(sessionId);
      const msgs: Msg[] = [];
      for (const t of d.turns) {
        if (t.input_type === "coach") msgs.push({ who: "coach", text: t.reply_text });
        else if (t.input_type === "practice") msgs.push({ who: "user", text: `（跟读：${t.practice_sentence}）` });
        else {
          const u = t.revised_text || t.user_text || t.transcript;
          if (u) msgs.push({ who: "user", text: u, turnId: t.turn_id });
          if (t.reply_text) msgs.push({ who: "coach", text: t.reply_text });
        }
      }
      setMessages(msgs);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function handleDispute(turnId: string) {
    try {
      await disputeReview(sessionId, turnId);
      const detail = await getSession(sessionId);
      setReview(detail.review);
      setErr("已标记'有误'，该建议不再用于记忆。");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function handlePracticeSentence(turnId: string) {
    if (busy) return;
    const selected = improvements.find(imp => String(imp.turn_id ?? "") === turnId);
    if (!selected || selected.disputed || sourceInvalid) return;
    if (!window.confirm(`即将练习：${String(selected.practice_sentence ?? "")}\n${String(selected.explanation_cn ?? "")}\n最长 5 分钟，一次性用于本次练习，不开启长期记忆。确认开始？`)) return;
    setBusy(true);
    setErr("");
    try {
      const s = await createPracticeSentence(sessionId, turnId);
      router.push(`/practice/${s.session_id}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function togglePause() {
    if (busy || sessionLoading || status === "ENDED") return;
    cancelRecord();
    document.querySelectorAll("audio").forEach(audio => audio.pause());
    setBusy(true); setErr(""); setShowHelp(false);
    try {
      if (status === "PAUSED") await resumeSession(sessionId); else await pauseSession(sessionId);
      const d = await getSession(sessionId); setStatus(d.status);
    } catch(e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  async function end() {
    if (busy || sessionLoading) return;
    cancelRecord();
    document.querySelectorAll("audio").forEach(audio => audio.pause());
    setShowHelp(false);
    setBusy(true);
    setErr("");
    try {
      const r = await endSession(sessionId);
      setStatus("ENDED");
      setPracticeSentence("");
      setReview(r.review);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const improvements = Array.isArray(review?.improvements) ? (review.improvements as Record<string, unknown>[]) : [];

  const ended = status === "ENDED";
  const paused = status === "PAUSED";
  const inputDisabled = busy || sessionLoading || ended || paused || !status;
  const answerCount = messages.filter(m => m.who === "user").length;

  return (
    <main className="page">
      <div className="topline"><button onClick={() => router.push("/")} className="text-link"><Icon name="back" size={16} /> 返回练习首页</button><span className="subtle-tag">{sceneLabel || "口语练习"}</span></div>
      <div className={styles.titleRow}><div><div className="eyebrow">ONE CONVERSATION AT A TIME</div><h1 className="page-title">{ended ? "给这次练习，留一点回味。" : "慢慢说，教练在听。"}</h1><p className="page-description">{ended ? "重看你的表达，找到下一次想尝试的小改变。" : "不必急着说得完美，先把你的想法说出来。"}</p></div><span className="badge" role="status"><span className={styles.statusDot} />{busy ? "正在处理…" : STATUS_CN[status] || (sessionLoading ? "加载中" : "暂不可用")}</span></div>
      <div className={styles.practiceLayout}>
        <div className={styles.conversation}>
          <div className={styles.conversationHeading}><span className="icon-tile"><Icon name="spark" size={20} /></span><div><h2>AI 口语教练</h2><p>专注你的表达，一起把话说清楚</p></div><span className={styles.turnCount}>{answerCount} 轮回答</span></div>
          {sourceInvalid && <div role="status" className="error-banner">该练习的来源建议已失效，请以新反馈为准。</div>}
          <div className={styles.messages} role="log" aria-label="练习对话">
            {sessionLoading && <div role="status" aria-label="加载对话"><div className="skeleton" /><div className="skeleton" /></div>}
            {messages.map((m,i) => <div key={i} className={`${styles.messageRow} ${m.who === "user" ? styles.userRow : ""}`}>
              {m.who === "coach" && <span className={styles.coachAvatar}><Icon name="spark" size={16} /></span>}
              <div className={`${styles.bubble} ${m.who === "user" ? styles.userBubble : ""}`}><div className={styles.messageLabel}>{m.who === "user" ? "你的回答" : "AI 教练"}</div><p>{m.text}</p>
                {m.who === "user" && m.turnId && <button disabled={busy} onClick={() => handleRevise(m.turnId!)} className={styles.revise}>修订转写</button>}
                {m.audioUrl && !ended && <AudioPlayer key={m.audioUrl} src={m.audioUrl} />}
              </div>
            </div>)}
            {busy && <div className={styles.thinking} role="status"><span /><span /><span /> 教练正在处理，请稍候…</div>}
            <div ref={bottomRef} />
          </div>

          {drill && !drill.completed && !ended && <section className={styles.readingCard}>
            <div className="section-head mt-0 mb-3"><h2><Icon name="book" size={19} /> 脱稿阶梯 · {drill.stage_label}</h2><span className="badge">逐级减少帮助</span></div>
            <p className={styles.practiceSentence}>{drill.visible_help}</p>
            {drill.drill_stage === "full_text" && <p className="text-sm muted mt-2">中文意思：{drill.drill_meaning}</p>}
            {drill.drill_stage === "free_speak" && <p className="text-sm muted mt-2">目标语块：{drill.drill_chunk}</p>}
            <textarea value={drillText} onChange={e => setDrillText(e.target.value)} placeholder="用英文说出这一句（不用逐字一样，意思对即可）…" disabled={busy} maxLength={2000} style={{ width: "100%", minHeight: "72px", padding: "10px 12px", borderRadius: "8px", border: "1px solid #d7ded4", fontSize: "15px", resize: "vertical" }} />
            <div className="flex items-center gap-2 flex-wrap mt-3"><button onClick={handleDrillAttempt} disabled={busy || !drillText.trim()} className="btn btn-primary">提交</button><button onClick={() => handleDrillAction("downgrade")} disabled={busy} className="btn btn-ghost">降一级</button><button onClick={() => handleDrillAction("skip")} disabled={busy} className="btn btn-ghost">跳过这级</button><button onClick={() => handleDrillAction("end")} disabled={busy} className="btn btn-ghost">结束</button></div>
            <p className="text-xs muted mt-3">看全文/补关键词=有参考练习；看结构=提示下完成；脱稿说=独立表达；换题=迁移证据。</p>
          </section>}
          {drill?.completed && <div className="notice mb-4"><Icon name="check" size={17} /><p>脱稿阶梯已完成。本次表达已写回待复练记录。</p></div>}

          {practiceSentence && !drill && !ended && <section className={styles.readingCard}>
            <div className="section-head mt-0 mb-3"><h2><Icon name="headphones" size={19} /> 试着练这一句</h2><span className="badge">有参考文本的短练</span></div>
            <p className={styles.practiceSentence}>{practiceSentence}</p>{practiceDemo && <AudioPlayer key={practiceDemo} src={practiceDemo} />}
            <div className="flex items-center gap-2 flex-wrap mt-4"><button onClick={() => toggleRecord("practice")} disabled={inputDisabled} className="btn btn-primary"><Icon name={recording && recordMode === "practice" ? "stop" : "mic"} size={16} />{recording && recordMode === "practice" ? "停止并提交" : "录音跟读"}</button><button onClick={handleSkipPractice} disabled={inputDisabled || recording} className="btn btn-ghost">跳过这次跟读</button>{recording && recordMode === "practice" && <><span className={styles.recordingTimer}>{timerText()}</span><button onClick={cancelRecord} className="btn btn-ghost">取消录音</button></>}</div>
            <p className="text-xs muted mt-3">可以直接尝试，也可以先听示范。跟读不等同于独立表达。</p>
          </section>}
          {practiceResult && <div className="notice mb-4"><Icon name="check" size={17} /><p>上次跟读：{practiceResult}<br /><span className="text-xs">仅供练习参考，不代表语言能力认证。</span></p></div>}
          {err && <p className="error-banner" role="alert">{err}{audioRetryTurnId && <button onClick={handleAudioRetry} disabled={busy} className="text-link ml-2">重试语音</button>}</p>}

          {ended ? <div className={styles.finished}><Icon name="check" size={22} /><div><strong>本次练习已结束</strong><p>对话已保留。你可以查看回顾，或回首页开始新练习。</p></div><button onClick={() => router.push("/history")} className="btn btn-secondary btn-small">查看历史<Icon name="arrow" size={14} /></button></div> : <div className={styles.composer}>
            {paused && <div className="notice mb-3"><Icon name="pause" size={16} /> 练习已暂停，点击“继续练习”后再回答。</div>}
            <label htmlFor="answer" className="sr-only">你的英文回答</label><textarea id="answer" value={text} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); sendText(); } }} placeholder={paused ? "练习已暂停…" : "用英文写下你的回答，或点击下方录音…"} disabled={inputDisabled || recording} maxLength={2000} />
            <div className={styles.composerTools}><button onClick={() => toggleRecord("answer")} disabled={inputDisabled || (recording && recordMode !== "answer")} className={`btn btn-primary ${recording && recordMode === "answer" ? styles.recording : ""}`}><Icon name={recording && recordMode === "answer" ? "stop" : "mic"} size={17} />{recording && recordMode === "answer" ? "停止并提交" : "录音回答"}</button>
              <button onClick={() => setShowHelp(!showHelp)} disabled={inputDisabled || recording} aria-expanded={showHelp} aria-controls="help-options" className="btn btn-secondary"><Icon name="help" size={16} />需要帮助</button>
              <button onClick={sendText} disabled={inputDisabled || recording || !text.trim()} className={`btn btn-secondary ${styles.send}`}><span>发送</span><Icon name="send" size={16} /></button>
            </div>
            {recording && recordMode === "answer" ? <div className={styles.recordingInfo}><span className={styles.recordingTimer}>{timerText()}</span><button onClick={cancelRecord} className="text-link">取消录音，不提交</button></div> : <div className={styles.inputNote}><span>每段录音最长 60 秒</span><span>Enter 发送 · Shift + Enter 换行</span></div>}
            {showHelp && <div id="help-options" className={styles.helpOptions}>
              <div className={styles.helpHeader}><span>在哪一步卡住了？</span><button onClick={() => setShowHelp(false)} aria-label="收起帮助" className="btn btn-ghost btn-small"><Icon name="close" size={15} /></button></div>
              {[{type:"repeat",title:"没听清",desc:"再听一次当前问题",icon:"headphones" as const},{type:"explain",title:"没听懂",desc:"用中文解释问题",icon:"chat" as const},{type:"organize",title:"不知道怎么组织",desc:"从关键词开始，逐步获得提示",icon:"spark" as const},{type:"translate",title:"中文转英文",desc:"尝试表达自己的中文意思",icon:"book" as const}].map(option => <button key={option.type} onClick={() => handleHelp(option.type)} disabled={inputDisabled} className={styles.helpOption}><Icon name={option.icon} size={18} /><span>{option.title}<small>{option.desc}</small></span><Icon name="chevron" size={14} /></button>)}
            </div>}
          </div>}
        </div>

        <aside className={styles.sessionAside}>
          <section className={`panel ${styles.goalCard}`}><span className="icon-tile"><Icon name="target" /></span><div className="eyebrow mt-4">TODAY’S FOCUS</div><h2>本次小目标</h2><p className={styles.goalText}>{goal || (sessionLoading ? "正在准备目标…" : "目标暂不可用")}</p><div className={styles.goalDivider} /><p className="text-xs muted">围绕本次小目标组织表达。暂时想不到时，可以向教练求助。</p></section>
          <section className={styles.tipCard}><Icon name="leaf" size={22} /><h3>给自己一点思考时间</h3><p>先说重点，再补充细节。<br />停顿不是问题，开口就是开始。</p></section>
          {!ended && <div className={styles.sessionActions}><button onClick={togglePause} disabled={busy || sessionLoading || !status} className="btn btn-secondary"><Icon name={paused ? "play" : "pause"} size={15} />{paused ? "继续练习" : "暂停练习"}</button><button onClick={end} disabled={busy || sessionLoading || !status} className="btn btn-ghost">结束练习</button></div>}
          <p className={styles.sessionNote}><Icon name="shield" size={13} /> 文字回答不参与发音评分</p>
        </aside>
      </div>

      {ended && <section className={`panel ${styles.review}`} aria-label="练习回顾">
        <div className="section-head mt-0"><div><div className="eyebrow">A MOMENT TO REFLECT</div><h2>练习回顾</h2></div><span className="icon-tile"><Icon name="book" /></span></div>
        {review ? improvements.length ? <div className={styles.reviewGrid}>{improvements.map((imp,i) => <article key={i} className={styles.reviewItem}><span className="badge badge-neutral">表达建议 {String(i+1).padStart(2,"0")}</span><div className={styles.reviewOriginal}><small>你的原句</small><p>{String(imp.original ?? "")}</p></div><p className="muted text-sm">{String(imp.explanation_cn ?? "")}</p><div className={styles.reviewSuggestion}><small>可以试着这样说</small><p>{String(imp.practice_sentence ?? imp.suggestion ?? "")}</p></div>{imp.disputed || sourceInvalid ? <p className="text-xs muted">已标记有误或来源已失效，待复核。</p> : <div className="flex gap-3 mt-3"><button onClick={() => handlePracticeSentence(String(imp.turn_id ?? ""))} disabled={busy} className="btn btn-primary btn-small"><Icon name="mic" size={14} />练这一句</button><button onClick={() => handleDispute(String(imp.turn_id ?? ""))} disabled={busy} className="btn btn-ghost btn-small">评价有误</button></div>}</article>)}</div> : <div className="notice"><Icon name="book" size={19} /><p>本次暂无可展示的改进建议。<br />这不代表整体能力已达标；可以回看对话，再尝试一段新的表达。</p></div> : <div className="notice"><Icon name="help" size={19} /><p>回顾暂未就绪，已提交的对话仍然保留。<button className="text-link ml-2" onClick={end} disabled={busy}>重新获取回顾</button></p></div>}
        <p className="text-xs muted mt-5">反馈仅供练习参考，不代表考试成绩或真实语言能力认证。</p>
      </section>}
    </main>
  );
}
