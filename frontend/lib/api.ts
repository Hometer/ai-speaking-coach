import type { ApiErrorBody, MemoryItem, SessionCreated, SessionDetail, SessionListItem, TurnDone } from "./types";

// 内测演示邀请码（真实邀请码上线前由产品经理分发）
const INVITE = "test-a";

function authHeaders(): Record<string, string> {
  return { "X-Invite-Code": INVITE };
}

async function handle<T>(res: Response): Promise<T> {
  const data = await res.json().catch(() => null);
  if (!res.ok || data === null) {
    const msg = (data as ApiErrorBody | null)?.error?.message ?? `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data as T;
}

export async function createSession(
  level: string,
  difficulty: string,
  durationMinutes = 5,
): Promise<SessionCreated> {
  const res = await fetch("/api/v1/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ level, difficulty, duration_minutes: durationMinutes }),
  });
  return handle<SessionCreated>(res);
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

export async function listSessions(): Promise<SessionListItem[]> {
  const res = await fetch("/api/v1/sessions", { headers: authHeaders() });
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

export async function getMemories(): Promise<MemoryItem[]> {
  const res = await fetch("/api/v1/users/me/memories", { headers: authHeaders() });
  return handle<MemoryItem[]>(res);
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

async function parseSSE<T>(res: Response): Promise<T> {
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
      if (event === "done") lastDone = data as T;
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
): Promise<TurnDone> {
  let url: string;
  let init: RequestInit;
  if (opts.audio) {
    url = `/api/v1/sessions/${sessionId}/turns/voice`;
    const fd = new FormData();
    fd.append("audio", opts.audio, "recording");
    init = { method: "POST", headers: authHeaders(), body: fd };
  } else {
    url = `/api/v1/sessions/${sessionId}/turns/text`;
    init = {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ text: opts.text ?? "" }),
    };
  }
  const res = await fetch(url, init);
  return parseSSE<TurnDone>(res);
}

export async function fetchAudio(url: string): Promise<string> {
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) throw new Error(`音频加载失败 HTTP ${res.status}`);
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}
