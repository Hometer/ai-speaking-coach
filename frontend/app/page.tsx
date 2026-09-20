"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { clearInviteCode, createSession, hasInviteCode, setInviteCode } from "@/lib/api";

export default function Home() {
  const router = useRouter();
  const [invite, setInvite] = useState("");
  // null = 尚未完成挂载读取；服务端与浏览器首次渲染都保持一致，避免 hydration mismatch
  const [loggedIn, setLoggedIn] = useState<boolean | null>(null);
  const [level, setLevel] = useState("basic");
  const [difficulty, setDifficulty] = useState("standard");
  const [duration, setDuration] = useState(5);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    let mounted = true;
    // 延迟到微任务读取 localStorage，避免在 effect 内同步 setState
    Promise.resolve().then(() => {
      if (mounted) setLoggedIn(hasInviteCode());
    });
    return () => {
      mounted = false;
    };
  }, []);

  function login() {
    if (!invite.trim()) {
      setErr("请输入邀请码");
      return;
    }
    setErr("");
    setInviteCode(invite);
    setLoggedIn(true);
  }

  function logout() {
    clearInviteCode();
    setInvite("");
    setLoggedIn(false);
  }

  async function start() {
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
    <main className="min-h-full flex flex-col items-center px-4 py-10">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold">AI 口语练习</h1>
          <p className="mt-2 text-sm" style={{ color: "var(--muted)" }}>
            和 AI 教练练一段英文面试自我介绍
          </p>
        </div>

        {loggedIn === null ? (
          <p className="text-sm text-center" style={{ color: "var(--muted)" }}>加载中…</p>
        ) : !loggedIn ? (
          <div className="rounded-2xl border p-6 shadow-sm" style={{ background: "var(--ai-bubble)", borderColor: "var(--line)" }}>
            <div className="text-sm font-medium mb-2">输入你的邀请码</div>
            <input
              value={invite}
              onChange={(e) => setInvite(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") login(); }}
              placeholder="例如 test-a"
              className="w-full rounded-lg border px-3 py-2 mb-4"
              style={{ borderColor: "var(--line)" }}
            />
            <button onClick={login} className="w-full rounded-xl px-4 py-3 font-semibold text-white" style={{ background: "var(--brand)" }}>
              进入
            </button>
            {err ? <p className="mt-3 text-sm" style={{ color: "var(--danger, #dc2626)" }}>{err}</p> : null}
          </div>
        ) : (
          <>
            <div className="rounded-2xl border p-6 shadow-sm" style={{ background: "var(--ai-bubble)", borderColor: "var(--line)" }}>
              <div className="text-sm font-medium mb-1">选择你的水平</div>
              <select value={level} onChange={(e) => setLevel(e.target.value)} className="w-full rounded-lg border px-3 py-2 mb-4" style={{ borderColor: "var(--line)" }}>
                <option value="beginner">入门</option>
                <option value="basic">基础</option>
                <option value="intermediate">中级</option>
              </select>

              <div className="text-sm font-medium mb-1">任务难度</div>
              <select value={difficulty} onChange={(e) => setDifficulty(e.target.value)} className="w-full rounded-lg border px-3 py-2 mb-4" style={{ borderColor: "var(--line)" }}>
                <option value="simple">简单</option>
                <option value="standard">标准</option>
              </select>

              <div className="text-sm font-medium mb-1">练习时长</div>
              <select value={duration} onChange={(e) => setDuration(Number(e.target.value))} className="w-full rounded-lg border px-3 py-2 mb-6" style={{ borderColor: "var(--line)" }}>
                <option value={5}>5 分钟（默认）</option>
                <option value={10}>10 分钟</option>
                <option value={15}>15 分钟（上限）</option>
              </select>

              <button onClick={start} disabled={loading} className="w-full rounded-xl px-4 py-3 font-semibold text-white disabled:opacity-50" style={{ background: "var(--brand)" }}>
                {loading ? "正在开始…" : "开始练习"}
              </button>
              {err ? <p className="mt-3 text-sm" style={{ color: "var(--danger, #dc2626)" }}>{err}</p> : null}
            </div>

            <div className="mt-4 text-center">
              <button onClick={logout} className="text-sm" style={{ color: "var(--muted)" }}>退出 / 切换邀请码</button>
            </div>

            <p className="mt-4 text-center text-xs" style={{ color: "var(--muted)" }}>
              首期仅开放「英文面试自我介绍」一个场景
            </p>
          </>
        )}
      </div>
    </main>
  );
}
