"use client";

import { useEffect, useState } from "react";
import { deleteSession, getSession, listSessions } from "@/lib/api";
import type { SessionDetail, SessionListItem } from "@/lib/types";

const EVAL_CN: Record<string, string> = {
  independent: "独立完成",
  hinted: "提示下完成",
  incomplete: "未完成",
  insufficient_evidence: "证据不足",
  not_started: "未开始",
};

export default function HistoryPage() {
  const [items, setItems] = useState<SessionListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [detail, setDetail] = useState<SessionDetail | null>(null);

  useEffect(() => {
    listSessions()
      .then(setItems)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  function reload() {
    setLoading(true);
    setErr("");
    listSessions()
      .then(setItems)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }

  async function view(id: string) {
    setErr("");
    try {
      setDetail(await getSession(id));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function del(id: string) {
    try {
      await deleteSession(id);
      setDetail(null);
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <main className="max-w-2xl mx-auto px-4 py-6">
      <h1 className="text-xl font-bold mb-4">练习历史</h1>

      {loading ? (
        <p className="text-sm" style={{ color: "var(--muted)" }}>加载中…</p>
      ) : items.length === 0 ? (
        <p className="text-sm" style={{ color: "var(--muted)" }}>还没有练习记录</p>
      ) : (
        <div className="space-y-3">
          {items.map((it) => (
            <div
              key={it.session_id}
              className="rounded-xl border p-4"
              style={{ background: "var(--ai-bubble)", borderColor: "var(--line)" }}
            >
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <div className="text-sm">
                  {it.started_at ? new Date(it.started_at).toLocaleString() : ""}
                  <span className="mx-2" style={{ color: "var(--muted)" }}>
                    {EVAL_CN[it.goal_status] || it.goal_status} · {it.turn_count} 轮
                  </span>
                </div>
                <div className="flex gap-2">
                  <button onClick={() => view(it.session_id)} className="text-sm px-3 py-1 rounded-lg text-white" style={{ background: "var(--brand)" }}>查看</button>
                  <button onClick={() => del(it.session_id)} className="text-sm px-3 py-1 rounded-lg" style={{ background: "#fdecea", color: "var(--danger, #dc2626)" }}>删除</button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {detail ? (
        <div className="mt-6 rounded-xl border p-4" style={{ background: "var(--ai-bubble)", borderColor: "var(--line)" }}>
          <div className="flex items-center justify-between mb-3">
            <div className="text-sm font-medium">对话与回顾</div>
            <button onClick={() => setDetail(null)} className="text-sm" style={{ color: "var(--muted)" }}>收起</button>
          </div>
          <div className="space-y-2">
            {detail.turns.map((t) => {
              if (t.input_type === "coach") {
                return <div key={t.turn_id} className="text-sm rounded-lg p-2" style={{ background: "var(--ai-bubble)", border: "1px solid var(--line)" }}>AI：{t.reply_text}</div>;
              }
              const u = t.user_text || t.transcript;
              return u ? <div key={t.turn_id} className="text-sm rounded-lg p-2" style={{ background: "var(--user-bubble)" }}>你：{u}</div> : null;
            })}
          </div>
          {detail.review && Array.isArray(detail.review.improvements) && (detail.review.improvements as Record<string, unknown>[]).length > 0 ? (
            <div className="mt-3 text-sm">
              <div className="mb-1" style={{ color: "var(--muted)" }}>改进建议</div>
              {(detail.review.improvements as Record<string, unknown>[]).map((imp, i) => (
                <div key={i} className="border-l-2 pl-2 mb-1" style={{ borderColor: "var(--brand)" }}>
                  {String(imp.explanation_cn ?? "")} · 练习句：{String(imp.practice_sentence ?? "")}
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      {err ? <p className="text-sm mt-3" style={{ color: "var(--danger, #dc2626)" }}>{err}</p> : null}
    </main>
  );
}
