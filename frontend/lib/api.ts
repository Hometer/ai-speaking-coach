import type { ApiErrorBody, DailyReviewAttemptResult, DailyReviewCurrent, DrillAttemptResult, DrillView, DueToday, MemoryItem, PhraseCard, Progress, QaSession, QueueItem, RiskHold, Scenario, SessionCreated, SessionDetail, SessionListItem, TrainingPlan, TurnDone } from "./types";

// 邀请码存本地，由用户输入，不再前端硬编码共享凭证
const INVITE_KEY = "invite_code";

export function getInviteCode(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(INVITE_KEY) || "";
}

export function setInviteCode(code: string): void {
  localStorage.setItem(INVITE_KEY, code.trim());
}

export function clearInviteCode(): void {
  localStorage.removeItem(INVITE_KEY);
}

export function hasInviteCode(): boolean {
  return !!getInviteCode();
}

function authHeaders(): Record<string, string> {
  return { "X-Invite-Code": getInviteCode() };
}

export async function intentKey(scope: string, payload: string | Blob): Promise<string> {
  const bytes = typeof payload === "string" ? new TextEncoder().encode(payload) : await payload.arrayBuffer();
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), (b) => b.toString(16).padStart(2, "0")).join("");
  const slot = `ai_intent:${scope}`;
  try {
    const saved = JSON.parse(sessionStorage.getItem(slot) || "null") as { digest?: string; key?: string } | null;
    if (saved?.digest === digest && saved.key) return saved.key;
  } catch { /* 私密模式下仍可提交，服务端保持幂等校验 */ }
  const key = crypto.randomUUID();
  try { sessionStorage.setItem(slot, JSON.stringify({ digest, key })); } catch { /* 同上 */ }
  return key;
}

export function clearIntentKey(scope: string): void {
  try { sessionStorage.removeItem(`ai_intent:${scope}`); } catch { /* 同上 */ }
}

async function handle<T>(res: Response): Promise<T> {
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (!res.ok) {
    const msg = (data as ApiErrorBody | null)?.error?.message ?? `HTTP ${res.status}`;
    throw new Error(msg);
  }
  // 200 + 合法 JSON（包括字面量 null）都按成功返回，避免 getPlan 等接口的空值被误报为"HTTP 200"。
  return data as T;
}

export async function listScenarios(): Promise<Scenario[]> {
  const res = await fetch("/api/v1/scenarios", { headers: authHeaders() });
  return handle<Scenario[]>(res);
}

export async function getScenario(sceneId: string): Promise<Scenario> {
  const res = await fetch(`/api/v1/scenarios/${sceneId}`, { headers: authHeaders() });
  return handle<Scenario>(res);
}

export async function recordDeviceCheck(status: string, summary: string): Promise<void> {
  const res = await fetch("/api/v1/device-check", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ status, summary }),
  });
  await handle<unknown>(res);
}

export async function createSession(
  level: string,
  difficulty: string,
  durationMinutes = 5,
  sceneId = "job_interview",
  planEntry?: { planId: number; planDay: number },
): Promise<SessionCreated> {
  const scope = "create-session";
  const requestKey = await intentKey(scope, JSON.stringify({ level, difficulty, durationMinutes, sceneId, planEntry }));
  const res = await fetch("/api/v1/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ level, difficulty, duration_minutes: durationMinutes, scene_id: sceneId, request_key: requestKey, plan_id: planEntry?.planId, plan_day: planEntry?.planDay }),
  });
  const result = await handle<SessionCreated>(res);
  clearIntentKey(scope);
  return result;
}

export async function createPracticeSentence(sourceSessionId: string, turnId: string): Promise<SessionCreated> {
  const scope = `sentence-create:${sourceSessionId}:${turnId}`;
  const key = await intentKey(scope, `${sourceSessionId}:${turnId}`);
  const res = await fetch("/api/v1/sessions/practice-sentence", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ source_session_id: sourceSessionId, turn_id: turnId, request_key: key }),
  });
  const result = await handle<SessionCreated>(res);
  clearIntentKey(scope);
  return result;
}

export async function getSession(sessionId: string): Promise<SessionDetail> {
  const res = await fetch(`/api/v1/sessions/${sessionId}`, { headers: authHeaders() });
  return handle<SessionDetail>(res);
}

export async function endSession(sessionId: string) {
  const res = await fetch(`/api/v1/sessions/${sessionId}/end`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ reason: "user_ended" }),
  });
  return handle<{ summary: Record<string, unknown>; review: Record<string, unknown> | null }>(res);
}

export async function listSessions(filters: Record<string, string> = {}): Promise<SessionListItem[]> {
  const qs = new URLSearchParams(filters).toString();
  const res = await fetch(`/api/v1/sessions${qs ? `?${qs}` : ""}`, { headers: authHeaders() });
  return handle<SessionListItem[]>(res);
}

export async function deleteSession(sessionId: string): Promise<void> {
  const res = await fetch(`/api/v1/sessions/${sessionId}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  await handle<unknown>(res);
}

export async function deleteAllSessions(): Promise<void> {
  const res = await fetch("/api/v1/sessions", {
    method: "DELETE",
    headers: authHeaders(),
  });
  await handle<unknown>(res);
}

export async function pauseSession(sessionId: string): Promise<void> {
  await handle(await fetch(`/api/v1/sessions/${sessionId}/pause`, { method: "POST", headers: authHeaders() }));
}

export async function resumeSession(sessionId: string): Promise<void> {
  await handle(await fetch(`/api/v1/sessions/${sessionId}/resume`, { method: "POST", headers: authHeaders() }));
}

export async function skipPractice(sessionId: string) {
  const res = await fetch(`/api/v1/sessions/${sessionId}/skip-practice`, { method: "POST", headers: authHeaders() });
  return handle<{ status: string; reply_text: string; reply_audio_url: string }>(res);
}

export async function reviseTurn(sessionId: string, turnId: string, revisedText: string): Promise<void> {
  const res = await fetch(`/api/v1/sessions/${sessionId}/turns/${turnId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ revised_text: revisedText }),
  });
  await handle<unknown>(res);
}

export async function requestHint(sessionId: string) {
  const res = await fetch(`/api/v1/sessions/${sessionId}/hint`, { method: "POST", headers: authHeaders() });
  return handle<{ reply_text: string; reply_audio_url: string; action_type: string }>(res);
}

export interface HelpResult {
  reply_text: string;
  reply_audio_url: string;
  help_type: string;
  help_level: number;
  is_answer_help: boolean;
  question_id: string;
}

export async function requestHelp(sessionId: string, helpType: string, sourceText?: string) {
  const body: Record<string, string> = { help_type: helpType };
  if (sourceText !== undefined) body.source_text = sourceText;
  const res = await fetch(`/api/v1/sessions/${sessionId}/help`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  });
  return handle<HelpResult>(res);
}

export async function disputeReview(sessionId: string, turnId: string): Promise<void> {
  const res = await fetch(`/api/v1/sessions/${sessionId}/review/dispute?turn_id=${encodeURIComponent(turnId)}`, {
    method: "POST",
    headers: authHeaders(),
  });
  await handle<unknown>(res);
}

export async function getMemories(): Promise<MemoryItem[]> {
  const res = await fetch("/api/v1/users/me/memories", { headers: authHeaders() });
  return handle<MemoryItem[]>(res);
}

export async function getMemorySettings(): Promise<{ enabled: boolean }> {
  return handle(await fetch("/api/v1/users/me/memory", { headers: authHeaders() }));
}

export async function toggleMemory(enabled: boolean): Promise<{ enabled: boolean }> {
  const res = await fetch("/api/v1/users/me/memory", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ enabled }),
  });
  return handle<{ enabled: boolean }>(res);
}

export async function deleteMemory(id: number): Promise<void> {
  const res = await fetch(`/api/v1/users/me/memories/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  await handle<unknown>(res);
}

export async function clearMemories(): Promise<void> {
  const res = await fetch("/api/v1/users/me/memories", {
    method: "DELETE",
    headers: authHeaders(),
  });
  await handle<unknown>(res);
}

export async function addToQueue(sourceSessionId: string, sourceTurnId: string): Promise<QueueItem> {
  const res = await fetch("/api/v1/review-queue", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ source_session_id: sourceSessionId, source_turn_id: sourceTurnId }),
  });
  return handle<QueueItem>(res);
}

export async function listQueue(): Promise<QueueItem[]> {
  const res = await fetch("/api/v1/review-queue", { headers: authHeaders() });
  return handle<QueueItem[]>(res);
}

export async function dueQueueItem(): Promise<QueueItem | null> {
  const res = await fetch("/api/v1/review-queue/due", { headers: authHeaders() });
  return handle<QueueItem | null>(res);
}

export async function retestQueueItem(id: number, answer: string, inputType = "text", attemptKey = ""): Promise<QueueItem> {
  const scope = `retest:${id}`;
  const key = attemptKey || await intentKey(scope, `${inputType}:${answer.trim()}`);
  const res = await fetch(`/api/v1/review-queue/${id}/retest`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ answer, input_type: inputType, attempt_key: key }),
  });
  const result = await handle<QueueItem>(res);
  clearIntentKey(scope);
  return result;
}

export async function revealQueueItem(id: number): Promise<QueueItem> {
  const res = await fetch(`/api/v1/review-queue/${id}/reveal`, { method: "POST", headers: authHeaders() });
  return handle<QueueItem>(res);
}

export async function disputeQueueItem(id: number): Promise<QueueItem> {
  const res = await fetch(`/api/v1/review-queue/${id}/dispute`, { method: "POST", headers: authHeaders() });
  return handle<QueueItem>(res);
}

export async function skipQueueItem(id: number): Promise<QueueItem> {
  const res = await fetch(`/api/v1/review-queue/${id}/skip`, { method: "POST", headers: authHeaders() });
  return handle<QueueItem>(res);
}

export async function deleteQueueItem(id: number): Promise<void> {
  const res = await fetch(`/api/v1/review-queue/${id}`, { method: "DELETE", headers: authHeaders() });
  await handle<unknown>(res);
}

export async function getProgress(): Promise<Progress> {
  const res = await fetch("/api/v1/progress", { headers: authHeaders() });
  return handle<Progress>(res);
}

export async function createPlan(goalType: string, availableMinutes: number): Promise<TrainingPlan> {
  const res = await fetch("/api/v1/training-plan", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ goal_type: goalType, available_minutes: availableMinutes }),
  });
  return handle<TrainingPlan>(res);
}

export async function getPlan(): Promise<TrainingPlan | null> {
  const res = await fetch("/api/v1/training-plan", { headers: authHeaders() });
  return handle<TrainingPlan | null>(res);
}

export async function skipPlanDay(day: number): Promise<TrainingPlan> {
  const res = await fetch(`/api/v1/training-plan/${day}/skip`, { method: "POST", headers: authHeaders() });
  return handle<TrainingPlan>(res);
}

export async function restartPlan(): Promise<TrainingPlan> {
  const res = await fetch("/api/v1/training-plan/restart", { method: "POST", headers: authHeaders() });
  return handle<TrainingPlan>(res);
}

export async function pausePlan(): Promise<TrainingPlan> {
  const res = await fetch("/api/v1/training-plan/pause", { method: "POST", headers: authHeaders() });
  return handle<TrainingPlan>(res);
}

export async function resumePlan(): Promise<TrainingPlan> {
  const res = await fetch("/api/v1/training-plan/resume", { method: "POST", headers: authHeaders() });
  return handle<TrainingPlan>(res);
}

export async function qaSessions(filters: Record<string, string> = {}): Promise<QaSession[]> {
  const query = new URLSearchParams(filters).toString();
  const res = await fetch(`/api/v1/qa/sessions${query ? `?${query}` : ""}`, { headers: authHeaders() });
  return handle<QaSession[]>(res);
}

export async function setQaRecordingConsent(sessionId: string, granted: boolean): Promise<{ granted: boolean; expires_at: string | null }> {
  const res = await fetch(`/api/v1/sessions/${sessionId}/qa-recording-consent`, {
    method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() }, body: JSON.stringify({ granted }),
  });
  return handle<{ granted: boolean; expires_at: string | null }>(res);
}

export async function completeQaRecording(id: number): Promise<void> {
  await handle(await fetch(`/api/v1/qa/recordings/${id}/complete`, { method: "POST", headers: authHeaders() }));
}

export async function qaRecordingAccessLog(id: number): Promise<{ reviewer_id: string; action: string; outcome: string; created_at: string }[]> {
  return handle(await fetch(`/api/v1/qa/recordings/${id}/access-log`, { headers: authHeaders() }));
}

export async function qaAddLabel(sessionId: string, turnId: string, issueType: string): Promise<unknown> {
  const res = await fetch("/api/v1/qa/labels", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ session_id: sessionId, turn_id: turnId, issue_type: issueType, conclusion: "confirmed" }),
  });
  return handle<unknown>(res);
}

export async function qaStats(): Promise<{ total: number; by_type: Record<string, number>; review_version: string }> {
  const res = await fetch("/api/v1/qa/stats", { headers: authHeaders() });
  return handle<{ total: number; by_type: Record<string, number>; review_version: string }>(res);
}

async function parseSSE<T>(res: Response, onChunk?: (text: string) => void): Promise<T> {
  if (!res.body) throw new Error("无响应流");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let lastDone: T | null = null;
  let lastError: string | null = null;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const raw = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let event = "message";
      let dataStr = "";
      for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataStr += line.slice(5).trim();
      }
      if (!dataStr) continue;
      const data = JSON.parse(dataStr);
      if (event === "chunk") {
        const text = (data as { text?: string }).text;
        if (text && onChunk) onChunk(text);
      } else if (event === "done") lastDone = data as T;
      else if (event === "risk_hold") lastDone = data as T;
      else if (event === "error") lastError = (data as ApiErrorBody).error?.message ?? "未知错误";
    }
  }
  if (lastError) throw new Error(lastError);
  if (lastDone === null) throw new Error("未收到结果");
  return lastDone;
}

export async function submitTurn(
  sessionId: string,
  opts: { text?: string; audio?: Blob },
  onChunk?: (text: string) => void,
): Promise<TurnDone | RiskHold> {
  const scope = `turn:${sessionId}`;
  const requestKey = await intentKey(scope, opts.audio || `text:${opts.text ?? ""}`);
  let url: string;
  let init: RequestInit;
  if (opts.audio) {
    url = `/api/v1/sessions/${sessionId}/turns/voice`;
    const fd = new FormData();
    fd.append("audio", opts.audio, "recording");
    fd.append("request_key", requestKey);
    init = { method: "POST", headers: authHeaders(), body: fd };
  } else {
    url = `/api/v1/sessions/${sessionId}/turns/text`;
    init = {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ text: opts.text ?? "", request_key: requestKey }),
    };
  }
  const res = await fetch(url, init);
  const result = await parseSSE<TurnDone | RiskHold>(res, onChunk);
  clearIntentKey(scope);
  return result;
}

export async function retryTurnAudio(sessionId: string, turnId: string): Promise<TurnDone> {
  const res = await fetch(`/api/v1/sessions/${sessionId}/turns/${turnId}/audio`, {
    method: "POST",
    headers: authHeaders(),
  });
  return handle<TurnDone>(res);
}

export async function discardRisk(sessionId: string, turnId: string): Promise<void> {
  const res = await fetch(`/api/v1/sessions/${sessionId}/turns/${turnId}/discard-risk`, { method: "POST", headers: authHeaders() });
  await handle<{ discarded: boolean }>(res);
}

export async function drillAttempt(sessionId: string, text: string, inputType = "text", attemptKey = ""): Promise<DrillAttemptResult> {
  const scope = `drill:${sessionId}`;
  const key = attemptKey || await intentKey(scope, `${inputType}:${text.trim()}`);
  const res = await fetch(`/api/v1/sessions/${sessionId}/drill-attempt`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ text, input_type: inputType, attempt_key: key }),
  });
  const result = await handle<DrillAttemptResult>(res);
  clearIntentKey(scope);
  return result;
}

export async function drillAction(sessionId: string, action: "skip" | "downgrade" | "jump" | "end"): Promise<DrillView> {
  const res = await fetch(`/api/v1/sessions/${sessionId}/drill/action`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ action }),
  });
  return handle<DrillView>(res);
}

export async function dueToday(): Promise<DueToday> {
  const res = await fetch("/api/v1/review-queue/due-today", { headers: authHeaders() });
  return handle<DueToday>(res);
}

export async function startDailyReview(): Promise<{ session_id: string | null; items: DueToday["items"] }> {
  const key = await intentKey("daily-start", new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Shanghai" }));
  const res = await fetch("/api/v1/review-queue/daily-review/start", { method: "POST", headers: { ...authHeaders(), "X-Request-Key": key } });
  return handle<{ session_id: string | null; items: DueToday["items"] }>(res);
}

export async function dailyReviewView(sessionId: string): Promise<DailyReviewCurrent> {
  const res = await fetch(`/api/v1/review-queue/daily-review/${sessionId}`, { headers: authHeaders() });
  return handle<DailyReviewCurrent>(res);
}

export async function dailyReviewAttempt(sessionId: string, answer: string, helpUsed: boolean, inputType = "text", attemptKey = ""): Promise<DailyReviewAttemptResult> {
  const scope = `daily:${sessionId}`;
  const key = attemptKey || await intentKey(scope, `${inputType}:${answer.trim()}`);
  const res = await fetch(`/api/v1/review-queue/daily-review/${sessionId}/attempt`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ answer, help_used: helpUsed, input_type: inputType, attempt_key: key }),
  });
  const result = await handle<DailyReviewAttemptResult>(res);
  clearIntentKey(scope);
  return result;
}

export async function dailyReviewAction(sessionId: string, action: "skip" | "defer" | "dispute" | "end"): Promise<{ next: DailyReviewCurrent }> {
  const res = await fetch(`/api/v1/review-queue/daily-review/${sessionId}/action`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ action }),
  });
  return handle<{ next: DailyReviewCurrent }>(res);
}

export async function createPhraseCard(sourceSessionId: string, sourceTurnId: string): Promise<PhraseCard> {
  const res = await fetch("/api/v1/phrase-cards", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ source_session_id: sourceSessionId, source_turn_id: sourceTurnId }),
  });
  return handle<PhraseCard>(res);
}

export async function listPhraseCards(): Promise<PhraseCard[]> {
  const res = await fetch("/api/v1/phrase-cards", { headers: authHeaders() });
  return handle<PhraseCard[]>(res);
}

export async function fetchAudio(url: string): Promise<string> {
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) throw new Error(`音频加载失败 HTTP ${res.status}`);
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

export async function updateMemory(id: number, text: string): Promise<void> {
  await handle(await fetch(`/api/v1/users/me/memories/${id}`, {method: "PATCH", headers: {"Content-Type":"application/json", ...authHeaders()}, body: JSON.stringify({text})}));
}

export function recordProductEvent(sessionId: string, action: "demo_play" | "recording_play" | "record_start" | "record_cancel" | "record_retry" | "review_view", key = crypto.randomUUID()): void {
  void fetch(`/api/v1/sessions/${sessionId}/events`, {method:"POST",headers:{"Content-Type":"application/json",...authHeaders()},body:JSON.stringify({action,request_key:key})}).catch(() => {});
}

export async function reviewConversation(turnId: string, answered: boolean, repeated_followup: boolean, topic_jump: boolean): Promise<void> {
  await handle(await fetch(`/api/v1/qa/conversation/${turnId}`, {method:"PATCH",headers:{"Content-Type":"application/json",...authHeaders()},body:JSON.stringify({answered,repeated_followup,topic_jump})}));
}

export async function qaLearningMetrics(): Promise<{eligible_users:number;active_review_users:number;active_review_rate:number|null;full_scene_review_users:number;pending_observation_users:number}> {
  return handle(await fetch("/api/v1/qa/learning-metrics",{headers:authHeaders()}));
}
