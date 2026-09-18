"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createSession } from "@/lib/api";

export default function Home() {
  const router = useRouter();
  const [level, setLevel] = useState("basic");
  const [difficulty, setDifficulty] = useState("standard");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  async function start() {
    setLoading(true);
    setErr("");
    try {
      const s = await createSession(level, difficulty);
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

        <div
          className="rounded-2xl border p-6 shadow-sm"
          style={{ background: "var(--ai-bubble)", borderColor: "var(--line)" }}
        >
          <div className="text-sm font-medium mb-1">选择你的水平</div>
          <select
            value={level}
            onChange={(e) => setLevel(e.target.value)}
            className="w-full rounded-lg border px-3 py-2 mb-4"
            style={{ borderColor: "var(--line)" }}
          >
            <option value="beginner">入门</option>
            <option value="basic">基础</option>
            <option value="intermediate">中级</option>
          </select>

          <div className="text-sm font-medium mb-1">练习时长</div>
          <select
            value={difficulty}
            onChange={(e) => setDifficulty(e.target.value)}
            className="w-full rounded-lg border px-3 py-2 mb-6"
            style={{ borderColor: "var(--line)" }}
          >
            <option value="simple">简单（约 3 分钟）</option>
            <option value="standard">标准（约 5 分钟）</option>
          </select>

          <button
            onClick={start}
            disabled={loading}
            className="w-full rounded-xl px-4 py-3 font-semibold text-white disabled:opacity-50"
            style={{ background: "var(--brand)" }}
          >
            {loading ? "正在开始…" : "开始练习"}
          </button>

          {err ? (
            <p className="mt-3 text-sm" style={{ color: "var(--danger, #dc2626)" }}>
              {err}
            </p>
          ) : null}
        </div>

        <p className="mt-6 text-center text-xs" style={{ color: "var(--muted)" }}>
          首期仅开放「英文面试自我介绍」一个场景
        </p>
      </div>
    </main>
  );
}
