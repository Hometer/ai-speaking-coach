import type { ApiErrorBody, SessionCreated, SessionDetail, TurnDone } from "./types";

const UID = "demo-user";

async function handle<T>(res: Response): Promise<T> {
  const data = await res.json().catch(() => null);
  if (!res.ok || data === null) {
    const msg = (data as ApiErrorBody | null)?.error?.message ?? `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data as T;
}

export async function createSession(level: string, difficulty: string): Promise<SessionCreated> {
  const res = await fetch("/api/v1/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-User-Id": UID },
    body: JSON.stringify({ level, difficulty }),
  });
  return handle<SessionCreated>(res);
}

export async function getSession(sessionId: string): Promise<SessionDetail> {
  const res = await fetch(`/api/v1/sessions/${sessionId}`, { headers: { "X-User-Id": UID } });
  return handle<SessionDetail>(res);
}

export async function endSession(sessionId: string) {
  const res = await fetch(`/api/v1/sessions/${sessionId}/end`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-User-Id": UID },
    body: JSON.stringify({ reason: "user_ended" }),
  });
  return handle<{ summary: Record<string, unknown>; review: Record<string, unknown> | null }>(res);
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
    init = { method: "POST", headers: { "X-User-Id": UID }, body: fd };
  } else {
    url = `/api/v1/sessions/${sessionId}/turns/text`;
    init = {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": UID },
      body: JSON.stringify({ text: opts.text ?? "" }),
    };
  }
  const res = await fetch(url, init);
  return parseSSE<TurnDone>(res);
}
