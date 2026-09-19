"use client";

import { useEffect, useState } from "react";
import { clearMemories, deleteMemory, getMemories, toggleMemory } from "@/lib/api";
import type { MemoryItem } from "@/lib/types";

export default function MemoryPage() {
  const [enabled, setEnabled] = useState(false);
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  useEffect(() => {
    getMemories()
      .then(setItems)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  function reload() {
    setLoading(true);
    setErr("");
    getMemories()
      .then(setItems)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }

  async function onToggle(e: React.ChangeEvent<HTMLInputElement>) {
    const next = e.target.checked;
    setErr("");
    try {
      const r = await toggleMemory(next);
      setEnabled(r.enabled);
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
    }
  }

  async function onDelete(id: number) {
    try {
      await deleteMemory(id);
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function onClear() {
    try {
      await clearMemories();
      reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <main className="max-w-2xl mx-auto px-4 py-6">
      <h1 className="text-xl font-bold mb-4">学习记忆</h1>
      <p className="text-sm mb-4" style={{ color: "var(--muted)" }}>
        默认关闭。开启后，结束练习时会把需要改进的表达存为候选记忆，供以后参考。
      </p>

      <div className="rounded-xl border p-4 mb-4" style={{ background: "var(--ai-bubble)", borderColor: "var(--line)" }}>
        <label className="flex items-center gap-2 text-sm font-medium">
          <input type="checkbox" checked={enabled} onChange={onToggle} />
          开启记忆
        </label>
      </div>

      {loading ? (
        <p className="text-sm" style={{ color: "var(--muted)" }}>加载中…</p>
      ) : items.length === 0 ? (
        <p className="text-sm" style={{ color: "var(--muted)" }}>暂无记忆</p>
      ) : (
        <div className="space-y-2">
          {items.map((m) => (
            <div key={m.id} className="rounded-xl border p-3 flex items-center justify-between gap-2" style={{ background: "var(--ai-bubble)", borderColor: "var(--line)" }}>
              <div className="text-sm">{m.text}</div>
              <button onClick={() => onDelete(m.id)} className="text-sm px-3 py-1 rounded-lg shrink-0" style={{ background: "#fdecea", color: "var(--danger, #dc2626)" }}>删除</button>
            </div>
          ))}
          <button onClick={onClear} className="text-sm px-3 py-2 rounded-lg" style={{ background: "#fdecea", color: "var(--danger, #dc2626)" }}>清空全部</button>
        </div>
      )}

      {err ? <p className="text-sm mt-3" style={{ color: "var(--danger, #dc2626)" }}>{err}</p> : null}
    </main>
  );
}
