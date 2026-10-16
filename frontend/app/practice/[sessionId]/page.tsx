"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { recoverPendingSubmissions, disputePronunciation, recordStage, startRecovery, recordProductEvent, addToQueue, clearIntentKey, createPhraseCard, createPracticeSentence, discardRisk, disputeReview, drillAction, drillAttempt, endSession, getInviteCode, getSession, intentKey, pauseSession, resumeSession, requestHelp, retryTurnAudio, reviseTurn, setQaRecordingConsent, skipPractice, submitTurn } from "@/lib/api";
import type { SessionDetail, DrillView, RiskHold, TurnItem } from "@/lib/types";
import AudioPlayer from "@/components/AudioPlayer";
import VoiceRisk from "@/components/VoiceRisk";
import { MediaLease } from "@/lib/mediaLease";
import ReviewSurvey from "@/components/ReviewSurvey";
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

const GOAL_STATUS_CN: Record<string, string> = {
  independent: "独立完成",
  hinted: "提示下完成",
  incomplete: "未完成",
  insufficient_evidence: "证据不足",
  text_completed: "文字完成",
  not_started: "未开始",
  revised: "待重算",
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
  const [lastPractice, setLastPractice] = useState<{ sentence: string; demoUrl: string; recordingUrl: string } | null>(null);
  const [slowDemo, setSlowDemo] = useState(false);
  const [slowRepeat, setSlowRepeat] = useState(false);
  const [practiceResult, setPracticeResult] = useState("");
  const [practiceEvaluation, setPracticeEvaluation] = useState<TurnItem["practice_eval"]>(null);
  const [practiceTurnId,setPracticeTurnId]=useState("");
  const [recording, setRecording] = useState(false);
  const [audioRetryTurnId, setAudioRetryTurnId] = useState<string | null>(null);
  const [drill, setDrill] = useState<DrillView | null>(null);
  const [drillText, setDrillText] = useState("");
  const [riskHold, setRiskHold] = useState<RiskHold | null>(null);
  const [qaConsent, setQaConsent] = useState(false);
  const [recordMode, setRecordMode] = useState<"answer" | "practice" | "drill" | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [review, setReview] = useState<Record<string, unknown> | null>(null);
  const [sourceInvalid, setSourceInvalid] = useState(false);
  const [sessionLoading, setSessionLoading] = useState(true);
  const [turns, setTurns] = useState<TurnItem[]>([]);

  const requestEpoch = useRef(0);
  const mediaLease = useRef(new MediaLease());
  const [riskRevision, setRiskRevision] = useState(0);
  const [repeatAudio, setRepeatAudio] = useState<{src: string; nonce: number} | null>(null);
  const [controlBusy, setControlBusy] = useState(false);
  const recRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const cancelRef = useRef(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const receive = useCallback((d: SessionDetail) => {
        setGoal(d.goal_text);
        setSceneLabel(d.scene_label || "");
        setStatus(d.status);
        setReview(d.review);
        setSourceInvalid(!!d.source_invalid);
        setDrill(d.drill);
        setTurns(d.turns);
        setRiskHold(d.pending_risk || null);
        setPracticeSentence(""); setPracticeDemo("");
        setPracticeEvaluation(null); setPracticeResult(""); setPracticeTurnId("");
        setQaConsent(!!d.qa_recording_consent);
        let lastPracticeIndex = -1;
        for (let i = 0; i < d.turns.length; i++) {
          if (d.turns[i].input_type === "practice") lastPracticeIndex = i;
        }
        if (lastPracticeIndex >= 0) {
          const practiced = d.turns[lastPracticeIndex];
          setPracticeTurnId(practiced.turn_id);
          const demo = d.turns.slice(0, lastPracticeIndex).reverse().find((t) =>
            t.action_type === "suggest_practice" && t.practice_sentence === practiced.practice_sentence,
          );
          const evaluation = practiced.practice_eval;
          setPracticeEvaluation(evaluation);
          setPracticeResult(evaluation ? (typeof evaluation.score === "number" ? `评测得分 ${evaluation.score}${evaluation.mock ? "（示例）" : "（实测）"}` : "本次无法评估") : "");
          setLastPractice({
            sentence: practiced.practice_sentence || "短句练习",
            demoUrl: demo?.reply_audio_url || "",
            recordingUrl: practiced.recording_url || "",
          });
        } else {
          setLastPractice(null);
        }
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
  }, []);

  useEffect(() => {
    let active = true;
    void recoverPendingSubmissions(sessionId);
    const recoveries=new Set<string>();
    let polling: ReturnType<typeof setInterval> | null = null;
    getSession(sessionId)
      .then((d) => {
        if (!active) return;
        if((d.in_flight || d.request_state === "uncertain") && d.active_operation_id && !recoveries.has(d.active_operation_id)) {
          recoveries.add(d.active_operation_id);
          void startRecovery(sessionId,d.active_operation_id).catch(()=>{});
        }
        receive(d);
        if (d.in_flight) {
          setBusy(true);
          polling = setInterval(() => { void getSession(sessionId).then(next => {
            if (active && !next.in_flight) { receive(next); setBusy(false); if (polling) clearInterval(polling); }
          }).catch(() => {}); }, 1000);
        }
        if (d.request_state === "uncertain") setErr("上次操作结果待核对。请查看已保存记录，同一请求不会自动重新调用模型。");
      })
      .catch((e) => { if (active) setErr(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (active) setSessionLoading(false); });
    return () => { active = false; if (polling) clearInterval(polling); };
  }, [sessionId, receive]);

  useEffect(()=>{
    if(drill && status!=="ENDED" && status!=="PAUSED") recordStage(sessionId,drill.drill_stage,"stage_view");
  },[sessionId,drill,status]);

  useEffect(() => {
    if (messages.length > 1) bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages]);

  useEffect(() => () => {
    requestEpoch.current++;
    mediaLease.current.cancel();
    cancelRef.current = true;
    if (timerRef.current) clearInterval(timerRef.current);
    if (recRef.current) {
      recRef.current.onstop = null;
      if (recRef.current.state === "recording") recRef.current.stop();
    }
    streamRef.current?.getTracks().forEach(track => track.stop());
  }, [sessionId]);

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
    const epoch = requestEpoch.current;
    setBusy(true);
    setErr("");
    setAudioRetryTurnId(null);
    let coachShown = false;
    try {
      if (typedText) { setText(""); addMsg("user", typedText); }
      const done = await submitTurn(sessionId, input, (text) => {
        if (epoch !== requestEpoch.current) return;
        coachShown = true;
        addMsg("coach", text); // chunk：教练文字生成后立即显示
      });
      if (epoch !== requestEpoch.current) return;
      if ("risk_hold" in done) {
        setRiskHold(done);
        return;
      }
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
      if (epoch !== requestEpoch.current) return;
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      if (epoch === requestEpoch.current) setBusy(false);
    }
  }

  async function confirmRisk(text: string, action: string) {
    if (!riskHold || busy) return;
    const epoch = requestEpoch.current;
    setBusy(true); setErr("");
    try {
      const res = await fetch(`/api/v1/sessions/${sessionId}/turns/${riskHold.turn_id}/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Invite-Code": getInviteCode() },
        body: JSON.stringify({ confirmed_text: text, action }),
      });
      const done = await res.json();
      if (epoch !== requestEpoch.current) return;
      if (!res.ok) { setErr(done?.error?.message ?? `HTTP ${res.status}`); return; }
      setRiskHold(null);
      addMsg("user", text, undefined, done.turn_id);
      addMsg("coach", done.reply_text, done.reply_audio_url, done.turn_id);
      setStatus(done.status);
      if (done.action_type === "suggest_practice" && done.practice_sentence) {
        setPracticeSentence(done.practice_sentence);
        setPracticeDemo(done.reply_audio_url);
      }
    } catch (e) {
      if (epoch !== requestEpoch.current) return;
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      if (epoch === requestEpoch.current) setBusy(false);
    }
  }

  async function rerecordRisk() {
    if (!riskHold || busy) return;
    const epoch = requestEpoch.current;
    setBusy(true); setErr("");
    try {
      await discardRisk(sessionId, riskHold.turn_id);
      setRiskHold(null);
    } catch (e) {
      if (epoch !== requestEpoch.current) return;
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      if (epoch === requestEpoch.current) setBusy(false);
    }
  }

  async function handleAudioRetry() {
    if (!audioRetryTurnId || busy) return;
    const epoch = requestEpoch.current;
    setBusy(true);
    setErr("");
    try {
      const r = await retryTurnAudio(sessionId, audioRetryTurnId);
      if (epoch !== requestEpoch.current) return;
      attachLastCoachAudio(r.reply_audio_url);
      if (r.audio_status === "failed") setErr("语音仍不可用，可以阅读文字。");
      else { setErr(""); setAudioRetryTurnId(null); }
    } catch (e) {
      if (epoch !== requestEpoch.current) return;
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      if (epoch === requestEpoch.current) setBusy(false);
    }
  }

  async function handleDrillAttempt() {
    cancelRecord();
    if (!drill || busy || !drillText.trim()) return;
    const epoch = requestEpoch.current;
    setBusy(true);
    setErr("");
    try {
      if(drill) recordStage(sessionId,drill.drill_stage,"stage_start");
      const r = await drillAttempt(sessionId, drillText.trim());
      if (epoch !== requestEpoch.current) return;
      setDrillText("");
      setDrill(r);
      if (r.completed) setStatus("ENDED");
      setErr(r.success ? (r.result === "independent" ? "脱稿成功，已记为本次独立表达。" : r.result === "transfer" ? "换题成功，已形成迁移证据。" : "说对了，进入下一级。") : "没对上，再试一次或降一级。");
    } catch (e) {
      if (epoch !== requestEpoch.current) return;
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      if (epoch === requestEpoch.current) setBusy(false);
    }
  }

  async function submitDrillVoice(blob: Blob) {
    const epoch = requestEpoch.current;
    setBusy(true);
    setErr("");
    try {
      const fd = new FormData();
      fd.append("audio", blob, "drill");
      const scope = `drill:${sessionId}`;
      fd.append("attempt_key", await intentKey(scope, blob));
      const res = await fetch(`/api/v1/sessions/${sessionId}/drill-attempt/voice`, {
        method: "POST",
        headers: { "X-Invite-Code": getInviteCode() },
        body: fd,
      });
      const data = await res.json();
      if (epoch !== requestEpoch.current) return;
      if (!res.ok) { setErr(data?.error?.message ?? `HTTP ${res.status}`); return; }
      clearIntentKey(scope);
      if (data.risk_hold) { setRiskRevision(v => v + 1); return; }
      setDrillText("");
      setDrill(data);
      if (data.completed) setStatus("ENDED");
      setErr(data.success ? (data.result === "independent" ? "脱稿成功，已记为本次独立表达。" : data.result === "transfer" ? "换题成功，已形成迁移证据。" : "说对了，进入下一级。") : "没对上，再试一次或降一级。");
    } catch (e) {
      if (epoch !== requestEpoch.current) return;
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      if (epoch === requestEpoch.current) setBusy(false);
    }
  }

  async function handleDrillAction(action: "skip" | "downgrade" | "jump" | "end" | "repeat") {
    if (!drill || busy) return;
    cancelRecord();
    const epoch = requestEpoch.current;
    setBusy(true);
    setErr("");
    try {
      const r = await drillAction(sessionId, action);
      if (epoch !== requestEpoch.current) return;
      setDrill(r);
      if (r.completed) setStatus("ENDED");
    } catch (e) {
      if (epoch !== requestEpoch.current) return;
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      if (epoch === requestEpoch.current) setBusy(false);
    }
  }

  function sendText() {
    const t = text.trim();
    if (!t || busy || recording || status === "ENDED" || status === "PAUSED" || sessionLoading) return;
    cancelRecord();
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

  async function toggleRecord(mode: "answer" | "practice" | "drill") {
    if (busy || sessionLoading || status === "ENDED" || status === "PAUSED") return;
    if (recRef.current && recRef.current.state === "recording") {
      cancelRef.current = false;
      recRef.current.stop();
      return;
    }
    const lease = mediaLease.current.begin();
    if (lease === null) return;
    if(mode==="answer") recordProductEvent(sessionId,"record_attempt");
    if(mode==="drill" && drill) recordStage(sessionId,drill.drill_stage,"stage_start");
    try {
      document.querySelectorAll("audio").forEach(audio => audio.pause());
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!mediaLease.current.accept(lease, stream)) return;
      streamRef.current = stream;
      if (chunksRef.current.length) recordProductEvent(sessionId, "record_retry");
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
        else if (mode === "practice") submitPractice(blob);
        else submitDrillVoice(blob);
      };
      rec.start();
      recordProductEvent(sessionId, "record_start");
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
      if (!mediaLease.current.fail(lease)) return;
      streamRef.current?.getTracks().forEach(track => track.stop());
      setErr("无法获取麦克风，可用文字输入。");
    }
  }

  function cancelRecord() {
    mediaLease.current.cancel();
    if (recRef.current && recRef.current.state === "recording") {
      recordProductEvent(sessionId, "record_cancel");
      cancelRef.current = true;
      recRef.current.stop();
    }
  }

  async function submitPractice(blob: Blob) {
    const epoch = requestEpoch.current;
    setBusy(true);
    setErr("");
    try {
      const fd = new FormData();
      fd.append("audio", blob, "practice");
      fd.append("sentence", practiceSentence);
      fd.append("request_key", await intentKey(`practice:${sessionId}:${practiceSentence}`, blob));
      const res = await fetch(`/api/v1/sessions/${sessionId}/practice`, {
        method: "POST",
        headers: { "X-Invite-Code": getInviteCode() },
        body: fd,
      });
      const data = await res.json();
      if (epoch !== requestEpoch.current) return;
      if (!res.ok) {
        setErr(data?.error?.message ?? `HTTP ${res.status}`);
        return;
      }
      clearIntentKey(`practice:${sessionId}:${practiceSentence}`);
      const score = typeof data.eval?.score === "number" ? data.eval.score : null;
      const isMock = !!data.eval?.mock;
      setPracticeResult(score === null ? "本次无法评估" : `评测得分 ${score}${isMock ? "（示例）" : "（实测）"}`);
      setPracticeEvaluation(data.eval);
      setPracticeTurnId(data.turn_id || "");
      setLastPractice({ sentence: practiceSentence, demoUrl: practiceDemo, recordingUrl: data.recording_url || "" });
      addMsg("coach", data.recheck_reply_text, data.recheck_audio_url);
      setStatus(data.status);
      setPracticeSentence("");
      setPracticeDemo("");
    } catch (e) {
      if (epoch !== requestEpoch.current) return;
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      if (epoch === requestEpoch.current) setBusy(false);
    }
  }

  async function handleSkipPractice() {
    const epoch = requestEpoch.current;
    setBusy(true);
    setErr("");
    try {
      const r = await skipPractice(sessionId);
      if (epoch !== requestEpoch.current) return;
      addMsg("coach", r.reply_text, r.reply_audio_url);
      setStatus(r.status);
      setPracticeSentence("");
      setPracticeDemo("");
    } catch (e) {
      if (epoch !== requestEpoch.current) return;
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      if (epoch === requestEpoch.current) setBusy(false);
    }
  }

  async function handleHelp(type: string) {
    const epoch = requestEpoch.current;
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
      if (epoch !== requestEpoch.current) return;
      if (type === "repeat") {
        // 重播已有音频，不追加新消息；可选慢速
        if (r.reply_audio_url) {
          setRepeatAudio({ src: r.reply_audio_url, nonce: Date.now() });
        }
      } else {
        addMsg("coach", r.reply_text, r.reply_audio_url);
      }
    } catch (e) {
      if (epoch !== requestEpoch.current) return;
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      if (epoch === requestEpoch.current) setBusy(false);
    }
  }

  async function handleRevise(turnId: string) {
    const epoch = requestEpoch.current;
    const newText = window.prompt("修改转写内容：", "");
    if (!newText || !newText.trim()) return;
    try {
      await reviseTurn(sessionId, turnId, newText.trim());
      if (epoch !== requestEpoch.current) return;
      setErr("已提交修订，该回合评分已作废。");
      const d = await getSession(sessionId);
      if (epoch === requestEpoch.current) receive(d);
    } catch (e) {
      if (epoch !== requestEpoch.current) return;
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function handlePhraseCard(turnId: string) {
    if (busy) return;
    setBusy(true);
    setErr("");
    try {
      await createPhraseCard(sessionId, turnId);
      router.push("/phrase-cards");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
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

  async function handleAddQueue(turnId: string) {
    if (busy) return;
    setBusy(true);
    setErr("");
    try {
      await addToQueue(sessionId, turnId);
      setErr("已加入待复练，到期后会安排再练一次。");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
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
    if (controlBusy || sessionLoading || status === "ENDED") return;
    requestEpoch.current += 1;
    cancelRecord();
    document.querySelectorAll("audio").forEach(audio => audio.pause());
    setControlBusy(true); setErr(""); setShowHelp(false);
    try {
      if (status === "PAUSED") await resumeSession(sessionId); else await pauseSession(sessionId);
      const d = await getSession(sessionId); receive(d);
    } catch(e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setControlBusy(false); setBusy(false); }
  }

  async function end() {
    if (controlBusy || sessionLoading) return;
    requestEpoch.current += 1;
    cancelRecord();
    document.querySelectorAll("audio").forEach(audio => audio.pause());
    setShowHelp(false);
    setControlBusy(true);
    setErr("");
    try {
      const r = await endSession(sessionId);
      setStatus("ENDED");
      setPracticeSentence("");
      setReview(r.review);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setControlBusy(false); setBusy(false);
    }
  }

  async function toggleQaConsent() {
    if (busy || sessionLoading) return;
    if (!qaConsent && !window.confirm("仅从现在开始，允许授权审核员使用本次练习的录音做内部质检？可随时撤回，最迟 7 天删除。此前录音不会补存。")) return;
    setBusy(true); setErr("");
    try { const result = await setQaRecordingConsent(sessionId, !qaConsent); setQaConsent(result.granted); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  const improvements = Array.isArray(review?.improvements) ? (review.improvements as Record<string, unknown>[]) : [];

  const ended = status === "ENDED";
  const paused = status === "PAUSED";
  const hideDrillAnswer = !!drill && (drill.material_available === false || ["free_speak", "transfer"].includes(drill.drill_stage));
  const inputDisabled = busy || sessionLoading || ended || paused || sourceInvalid || !status;
  const drillBlocked = sourceInvalid || drill?.material_available === false;
  const answerCount = messages.filter(m => m.who === "user").length;
  // 跟读 / 脱稿阶梯进行时，收起普通输入区，只保留当前任务的固定操作，避免两套录音按钮并存。
  const inPractice = !!practiceSentence && !drill && !ended;
  const inDrill = !!drill && !drill.completed && !ended;
  const showComposer = !ended && !inPractice && !inDrill;
  const practiceTurns = turns.filter((t) => t.input_type === "practice");

  return (
    <main className="page">
      <div className="topline"><button onClick={() => router.push("/")} className="text-link"><Icon name="back" size={16} /> 返回练习首页</button><span className="subtle-tag">{sceneLabel || "口语练习"}</span></div>
      <div className={styles.titleRow}><div><div className="eyebrow">ONE CONVERSATION AT A TIME</div><h1 className="page-title">{ended ? "给这次练习，留一点回味。" : "慢慢说，教练在听。"}</h1><p className="page-description">{ended ? "重看你的表达，找到下一次想尝试的小改变。" : "不必急着说得完美，先把你的想法说出来。"}</p></div><span className="badge" role="status"><span className={styles.statusDot} />{busy ? "正在处理…" : STATUS_CN[status] || (sessionLoading ? "加载中" : "暂不可用")}</span></div>
      <div className="notice mb-4"><Icon name="shield" size={16} /><span>内部质检录音需单独同意。{qaConsent ? "已授权，可随时撤回并删除质检副本。" : "当前未授权，普通录音不会作为质检材料保留。"}</span><button className="btn btn-ghost btn-small" onClick={toggleQaConsent} disabled={busy || sessionLoading || (ended && !qaConsent)}>{qaConsent ? "撤回质检录音授权" : "同意本次录音用于质检"}</button></div>
      <div className={styles.practiceLayout}>
        <div className={styles.conversation}>
          <div className={styles.conversationHeading}><span className="icon-tile"><Icon name="spark" size={20} /></span><div><h2>AI 口语教练</h2><p>专注你的表达，一起把话说清楚</p></div><span className={styles.turnCount}>{answerCount} 轮回答</span>{!ended && <div className={styles.headerActions}><button onClick={togglePause} disabled={controlBusy || sessionLoading || !status} className="btn btn-ghost btn-small"><Icon name={paused ? "play" : "pause"} size={14} />{paused ? "继续" : "暂停"}</button><button onClick={end} disabled={controlBusy || sessionLoading || !status} className="btn btn-ghost btn-small"><Icon name="stop" size={14} />结束练习</button></div>}</div>
          {sourceInvalid && <div role="status" className="error-banner">该练习的来源建议已失效，请以新反馈为准。</div>}
          <div className={styles.messages} role="log" aria-label="练习对话">
            {sessionLoading && <div role="status" aria-label="加载对话"><div className="skeleton" /><div className="skeleton" /></div>}
            {(hideDrillAnswer ? [] : messages).map((m,i) => <div key={i} className={`${styles.messageRow} ${m.who === "user" ? styles.userRow : ""}`}>
              {m.who === "coach" && <span className={styles.coachAvatar}><Icon name="spark" size={16} /></span>}
              <div className={`${styles.bubble} ${m.who === "user" ? styles.userBubble : ""}`}><div className={styles.messageLabel}>{m.who === "user" ? "你的回答" : "AI 教练"}</div><p>{m.text}</p>
                {m.who === "user" && m.turnId && <button disabled={busy} onClick={() => handleRevise(m.turnId!)} className={styles.revise}>修订转写</button>}
                {m.audioUrl && !ended && <AudioPlayer key={m.audioUrl} src={m.audioUrl} />}
              </div>
            </div>)}
            {busy && <div className={styles.thinking} role="status"><span /><span /><span /> 教练正在处理，请稍候…</div>}
            <div ref={bottomRef} />
          </div>

          {repeatAudio && !paused && !ended && <div className="panel panel-padding"><p>再听一次当前问题</p><AudioPlayer key={repeatAudio.nonce} src={repeatAudio.src} rate={slowRepeat ? 0.75 : 1} autoPlay /></div>}
          {drill && <VoiceRisk key={sessionId} scope="drill_voice" objectId={sessionId} revision={riskRevision} disabled={busy || paused || ended} onResolved={async () => { cancelRecord(); receive(await getSession(sessionId)); }} />}
          {drill?.recording && <section className="panel panel-padding mt-3" aria-label="单句录音对比"><h3>本次录音与示范</h3><p className="text-xs muted">对应录制阶段：{({full_text:"看全文",fill_blanks:"补关键词",structure:"看结构",free_speak:"脱稿说",transfer:"换题用"} as Record<string,string>)[drill.recording.stage] || "单句练习"}。普通录音到期后自动清理。</p><div className={styles.reviewAudioItem}><div><p>本人录音</p>{drill.recording.recording_url ? <AudioPlayer key={drill.recording.recording_url} src={drill.recording.recording_url} kind="recording_play" /> : <p className="text-xs muted">录音已清理或本次未保存</p>}</div><div><p>示范表达</p>{drill.recording.demo_audio_url ? <><p>{drill.recording.reference_sentence}</p><AudioPlayer src={drill.recording.demo_audio_url} rate={slowDemo ? 0.75 : 1} /><label><input type="checkbox" checked={slowDemo} onChange={e => setSlowDemo(e.target.checked)} /> 慢速示范</label></> : <p className="text-xs muted">脱稿答题期间隐藏参考，完成后可对比。</p>}</div></div>{!ended && <button className="btn btn-secondary mt-3" disabled={inputDisabled || recording} onClick={() => handleDrillAction("repeat")}>重录上一次</button>}</section>}
          {riskHold && <section className={styles.readingCard}>
            <div className="section-head mt-0 mb-3"><h2><Icon name="mic" size={19} /> 核对一下你说的话</h2><span className="badge">识别可能不准</span></div>
            <p className={styles.practiceSentence}>{riskHold.transcript}</p>
            <p className="text-sm muted mt-2">系统觉得这段转写可能不完整或含杂音。你可以听回、确认、修改、改用文字，或重说一遍。</p>
            {riskHold.recording_url && <div className="mt-2"><p className="text-xs muted">听回你的录音：</p><AudioPlayer key={riskHold.recording_url} src={riskHold.recording_url} kind="recording_play" /></div>}
            <div className="flex items-center gap-2 flex-wrap mt-3">
              <button onClick={() => confirmRisk(riskHold.transcript, "confirm")} disabled={busy} className="btn btn-primary">确认无误</button>
              <button onClick={() => { const t = window.prompt("请修改转写：", riskHold.transcript); if (t && t.trim()) confirmRisk(t.trim(), "revise"); }} disabled={busy} className="btn btn-secondary">修改文字</button>
              <button onClick={() => { const t = window.prompt("改用文字回答：", riskHold.transcript); if (t && t.trim()) confirmRisk(t.trim(), "text_fallback"); }} disabled={busy} className="btn btn-secondary">改用文字</button>
              <button onClick={rerecordRisk} disabled={busy} className="btn btn-ghost">重说一遍</button>
            </div>
          </section>}

          {drill && !drill.completed && !ended && <section className={styles.readingCard}>
            <div className="section-head mt-0 mb-3"><h2><Icon name="book" size={19} /> 脱稿阶梯 · {drill.stage_label}</h2><span className="badge">逐级减少帮助</span></div>
            <p className={styles.practiceSentence}>{drill.visible_help}</p>
            {drill.drill_stage === "full_text" && <p className="text-sm muted mt-2">中文意思：{drill.drill_meaning}</p>}

            <textarea value={drillText} onChange={e => setDrillText(e.target.value)} placeholder={drill.drill_stage === "free_speak" || drill.drill_stage === "transfer" ? "这一级需要语音：点击下方「录音说出」…" : "用英文说出这一句（不用逐字一样，意思对即可）…"} disabled={busy || drillBlocked || paused} maxLength={2000} style={{ width: "100%", minHeight: "72px", padding: "10px 12px", borderRadius: "8px", border: "1px solid #d7ded4", fontSize: "15px", resize: "vertical" }} />
            <div className="flex items-center gap-2 flex-wrap mt-3">{<button onClick={() => toggleRecord("drill")} disabled={inputDisabled || drillBlocked} className="btn btn-primary"><Icon name={recording && recordMode === "drill" ? "stop" : "mic"} size={16} />{recording && recordMode === "drill" ? "停止并提交" : "录音说出"}</button>}{!["free_speak", "transfer"].includes(drill.drill_stage) && <button onClick={handleDrillAttempt} disabled={busy || drillBlocked || paused || recording || !drillText.trim()} className="btn btn-primary">提交</button>}<button onClick={() => handleDrillAction("downgrade")} disabled={busy || drillBlocked || paused} className="btn btn-ghost">降一级</button><button onClick={() => handleDrillAction("skip")} disabled={busy || drillBlocked || paused} className="btn btn-ghost">跳过这级</button>{drill.drill_stage !== "transfer" && <button onClick={() => handleDrillAction("jump")} disabled={busy || drillBlocked || paused} className="btn btn-ghost">跳级到换题</button>}<button onClick={() => handleDrillAction("end")} disabled={busy} className="btn btn-ghost">结束</button>{recording && recordMode === "drill" && <span className={styles.recordingTimer}>{timerText()}</span>}</div>
            <p className="text-xs muted mt-3">看全文/补关键词=有参考练习；看结构=提示下完成；脱稿说=独立表达；换题=迁移证据。</p>
          </section>}
          {drill?.completed && <div className="notice mb-4"><Icon name="check" size={17} /><p>{drill.outcome === "transfer" ? "换题成功，已形成迁移证据，写回待复练记录。" : drill.outcome === "independent" ? "脱稿完成，已记为本次独立表达。" : "脱稿练习已结束。独立/换题结果会按帮助证据写回待复练。"}</p></div>}

          {practiceSentence && !drill && !ended && <section className={styles.readingCard}>
            <div className="section-head mt-0 mb-3"><h2><Icon name="headphones" size={19} /> 试着练这一句</h2><span className="badge">有参考文本的短练</span></div>
            <p className={styles.practiceSentence}>{practiceSentence}</p>
            {practiceDemo && <div><AudioPlayer key={practiceDemo} src={practiceDemo} rate={slowDemo ? 0.75 : 1} /><button onClick={() => setSlowDemo(!slowDemo)} className="btn btn-ghost btn-small mt-1">{slowDemo ? "正常速度" : "慢速示范"}</button></div>}
            <div className="flex items-center gap-2 flex-wrap mt-4"><button onClick={() => toggleRecord("practice")} disabled={inputDisabled} className="btn btn-primary"><Icon name={recording && recordMode === "practice" ? "stop" : "mic"} size={16} />{recording && recordMode === "practice" ? "停止并提交" : "录音跟读"}</button><button onClick={handleSkipPractice} disabled={inputDisabled || recording} className="btn btn-ghost">跳过这次跟读</button>{recording && recordMode === "practice" && <><span className={styles.recordingTimer}>{timerText()}</span><button onClick={cancelRecord} className="btn btn-ghost">取消录音</button></>}</div>
            <p className="text-xs muted mt-3">可以直接尝试，也可以先听示范。跟读不等同于独立表达。</p>
          </section>}
          {lastPractice && !drill && <section className={styles.readingCard} aria-label="刚才的跟读录音">
            <div className="section-head mt-0 mb-3"><h2><Icon name="headphones" size={19} /> 刚才的跟读 · 可听回</h2></div>
            <p className={styles.practiceSentence}>{lastPractice.sentence}</p>
            <div className="mt-3"><p className="text-xs muted">示范表达</p>{lastPractice.demoUrl ? <AudioPlayer key={lastPractice.demoUrl} src={lastPractice.demoUrl} rate={slowDemo ? 0.75 : 1} /> : <p className="text-xs muted">示范暂不可用</p>}</div>
            <div className="mt-3"><p className="text-xs muted">你的录音（本次评价使用）</p>{lastPractice.recordingUrl ? <AudioPlayer key={lastPractice.recordingUrl} src={lastPractice.recordingUrl} kind="recording_play" /> : <p className="text-xs muted">录音已清理或本次未录音</p>}</div>
            {practiceEvaluation?.word_items?.slice(0,3).map((item, i) => <p className="text-xs muted" key={i}>{item.word}：{item.score == null ? "供应商未返回局部分数" : item.score}</p>)}
            {practiceEvaluation?.word_items?.length && practiceTurnId ? <button className="btn btn-ghost btn-small" onClick={async()=>{try{await disputePronunciation(sessionId,practiceTurnId);setPracticeEvaluation(value=>value?{...value,disputed:true}:value);setPracticeResult("这次词级反馈已标记有争议，待复核。");}catch(e){setErr(e instanceof Error?e.message:String(e));}}}>词级反馈有误</button> : null}
            {practiceEvaluation?.disputed && <p className="text-xs muted">这次词级反馈已标记争议，待复核。</p>}
            {practiceResult && <p className="text-xs muted mt-3">{practiceResult} · 仅供练习参考</p>}
          </section>}
          {err && <p className="error-banner" role="alert">{err}{audioRetryTurnId && <button onClick={handleAudioRetry} disabled={busy} className="text-link ml-2">重试语音</button>}</p>}

          {ended ? <div className={styles.finished}><Icon name="check" size={22} /><div><strong>本次练习已结束</strong><p>对话已保留。你可以查看回顾，或回首页开始新练习。</p></div><button onClick={() => router.push("/history")} className="btn btn-secondary btn-small">查看历史<Icon name="arrow" size={14} /></button></div> : showComposer ? <div className={styles.composer}>
            {paused && <div className="notice mb-3"><Icon name="pause" size={16} /> 练习已暂停，点击“继续练习”后再回答。</div>}
            <label htmlFor="answer" className="sr-only">你的英文回答</label><textarea id="answer" value={text} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); sendText(); } }} placeholder={paused ? "练习已暂停…" : "用英文写下你的回答，或点击下方录音…"} disabled={inputDisabled || recording} maxLength={2000} />
            <div className={styles.composerTools}><button onClick={() => toggleRecord("answer")} disabled={inputDisabled || (recording && recordMode !== "answer")} className={`btn btn-primary ${recording && recordMode === "answer" ? styles.recording : ""}`}><Icon name={recording && recordMode === "answer" ? "stop" : "mic"} size={17} />{recording && recordMode === "answer" ? "停止并提交" : "录音回答"}</button>
              <button onClick={() => setShowHelp(!showHelp)} disabled={inputDisabled || recording} aria-expanded={showHelp} aria-controls="help-options" className="btn btn-secondary"><Icon name="help" size={16} />需要帮助</button>
              <button onClick={sendText} disabled={inputDisabled || recording || !text.trim()} className={`btn btn-secondary ${styles.send}`}><span>发送</span><Icon name="send" size={16} /></button>
            </div>
            {recording && recordMode === "answer" ? <div className={styles.recordingInfo}><span className={styles.recordingTimer}>{timerText()}</span><button onClick={cancelRecord} className="text-link">取消录音，不提交</button></div> : <div className={styles.inputNote}><span>每段录音最长 60 秒</span><span>Enter 发送 · Shift + Enter 换行</span></div>}
            {showHelp && <div id="help-options" className={styles.helpOptions}>
              <div className={styles.helpHeader}><span>在哪一步卡住了？</span><div className="flex gap-2"><button onClick={() => setSlowRepeat(!slowRepeat)} aria-pressed={slowRepeat} className="btn btn-ghost btn-small">{slowRepeat ? "正常速度" : "慢速重播"}</button><button onClick={() => setShowHelp(false)} aria-label="收起帮助" className="btn btn-ghost btn-small"><Icon name="close" size={15} /></button></div></div>
              {[{type:"repeat",title:"没听清",desc:"再听一次当前问题",icon:"headphones" as const},{type:"explain",title:"没听懂",desc:"用中文解释问题",icon:"chat" as const},{type:"organize",title:"不知道怎么组织",desc:"从关键词开始，逐步获得提示",icon:"spark" as const},{type:"translate",title:"中文转英文",desc:"尝试表达自己的中文意思",icon:"book" as const}].map(option => <button key={option.type} onClick={() => handleHelp(option.type)} disabled={inputDisabled} className={styles.helpOption}><Icon name={option.icon} size={18} /><span>{option.title}<small>{option.desc}</small></span><Icon name="chevron" size={14} /></button>)}
            </div>}
          </div> : null}
        </div>

        <aside className={styles.sessionAside}>
          <section className={`panel ${styles.goalCard}`}><span className="icon-tile"><Icon name="target" /></span><div className="eyebrow mt-4">TODAY’S FOCUS</div><h2>本次小目标</h2><p className={styles.goalText}>{goal || (sessionLoading ? "正在准备目标…" : "目标暂不可用")}</p><div className={styles.goalDivider} /><p className="text-xs muted">围绕本次小目标组织表达。暂时想不到时，可以向教练求助。</p></section>
          <section className={styles.tipCard}><Icon name="leaf" size={22} /><h3>给自己一点思考时间</h3><p>先说重点，再补充细节。<br />停顿不是问题，开口就是开始。</p></section>
          <p className={styles.sessionNote}><Icon name="shield" size={13} /> 文字回答不参与发音评分</p>
        </aside>
      </div>

      {ended && <section className={`panel ${styles.review}`} aria-label="本次练习复盘">
        <div className="section-head mt-0"><div><div className="eyebrow">THIS SESSION</div><h2>本次练习复盘</h2></div><span className="icon-tile"><Icon name="book" /></span></div>

        {review && <div className={styles.reviewGoal}><span className="badge">小目标</span><p>{goal || "本次小目标"}</p>{review.goal_status ? <span className={`badge ${review.goal_status === "independent" ? "" : "badge-neutral"}`}>{GOAL_STATUS_CN[String(review.goal_status)] || String(review.goal_status)}</span> : null}</div>}

        {practiceTurns.length > 0 && <div className={styles.reviewSubBlock}>
          <div className="section-head mt-0"><h3><Icon name="headphones" size={17} /> 示范与本人录音</h3><span>相邻播放，听出差异</span></div>
          <div className={styles.reviewAudioList}>{practiceTurns.map((t, i) => <div key={t.turn_id} className={styles.reviewAudioItem}><div><small>示范表达 {String(i + 1).padStart(2, "0")}</small><p>{t.practice_sentence || "短句练习"}</p>{t.reply_audio_url ? <AudioPlayer key={t.reply_audio_url} src={t.reply_audio_url} /> : <p className="text-xs muted">示范暂不可用</p>}</div><div><small>本人录音 {String(i + 1).padStart(2, "0")}</small>{t.recording_url ? <AudioPlayer key={t.recording_url} src={t.recording_url} kind="recording_play" /> : <p className="text-xs muted">录音已清理或本次未录音</p>}</div></div>)}</div>
          <p className="text-xs muted mt-3">发音评测只在跟读时给出；这里只做听感对比，不生成新的评分。</p>
        </div>}

        <div className="section-head mt-5"><h3><Icon name="book" size={17} /> 表达建议</h3><span>最多两条主要建议</span></div>
        {review && !review.feedback_version && <p className="text-xs muted">这是历史版本回顾，建议尚未按新的原回合反馈口径核验。</p>}
        {review ? improvements.length ? <div className={styles.reviewGrid}>{improvements.map((imp,i) => <article key={i} className={styles.reviewItem}><span className="badge badge-neutral">表达建议 {String(i+1).padStart(2,"0")}</span>{imp.feedback_source === "mock_fixture" && <p className="text-xs muted">离线固定样例反馈，真实效果待验。</p>}<div className={styles.reviewOriginal}><small>你的原句</small><p>{String(imp.original ?? "")}</p></div><p className="muted text-sm">{String(imp.explanation_cn ?? "")}</p><div className={styles.reviewSuggestion}><small>可以试着这样说</small><p>{String(imp.practice_sentence ?? imp.suggestion ?? "")}</p></div>{imp.disputed || sourceInvalid ? <p className="text-xs muted">已标记有误或来源已失效，待复核。</p> : imp.practice_available === false ? <div><p className="text-xs muted">这条建议的复练材料尚未准备好，可先参考纠错说明。</p><button onClick={() => handleDispute(String(imp.turn_id ?? ""))} disabled={busy} className="btn btn-ghost btn-small">评价有误</button></div> : <div className={styles.reviewActions}><button onClick={() => handlePracticeSentence(String(imp.turn_id ?? ""))} disabled={busy} className="btn btn-primary btn-small"><Icon name="mic" size={14} />练这一句</button><button onClick={() => handlePhraseCard(String(imp.turn_id ?? ""))} disabled={busy} className="btn btn-ghost btn-small"><Icon name="book" size={14} />语块卡</button><button onClick={() => handleAddQueue(String(imp.turn_id ?? ""))} disabled={busy} className="btn btn-ghost btn-small"><Icon name="check" size={14} />加入待复练</button><button onClick={() => handleDispute(String(imp.turn_id ?? ""))} disabled={busy} className="btn btn-ghost btn-small">评价有误</button></div>}</article>)}</div> : <div className="notice"><Icon name="book" size={19} /><p>本次暂无可展示的改进建议。<br />这不代表整体能力已达标；可以回看对话，再尝试一段新的表达。</p></div> : <div className="notice"><Icon name="help" size={19} /><p>回顾暂未就绪，已提交的对话仍然保留。<button className="text-link ml-2" onClick={end} disabled={busy}>重新获取回顾</button></p></div>}

        {drill && drill.completed && <div className={styles.reviewSubBlock}><div className="section-head mt-0"><h3><Icon name="book" size={17} /> 脱稿练习</h3><span className="badge badge-neutral">{drill.outcome === "transfer" ? "迁移成功" : drill.outcome === "independent" ? "独立完成" : "已结束"}</span></div><p className="text-sm muted">{drill.outcome === "transfer" ? "换题成功，已形成迁移证据，并写回待复练记录。" : drill.outcome === "independent" ? "脱稿完成，已记为本次独立表达。" : "本次脱稿练习已结束，结果按帮助证据记录。"}</p></div>}

        <p className="text-xs muted mt-5">反馈仅供练习参考，不代表考试成绩或真实语言能力认证。</p>
      </section>}
    {ended && !drill && review && !review.stale && Array.isArray(review.evidence) && review.evidence.length>0 && <ReviewSurvey sessionId={sessionId}/>}
    </main>
  );
}
