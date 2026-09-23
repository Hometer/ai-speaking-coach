"use client";

import { useEffect, useState } from "react";
import { listPhraseCards } from "@/lib/api";
import type { PhraseCard } from "@/lib/types";
import Icon from "@/components/Icon";

export default function PhraseCardsPage() {
  const [cards, setCards] = useState<PhraseCard[] | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    listPhraseCards().then(setCards).catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <main className="page">
      <section className="panel">
        <div className="section-head">
          <h2><Icon name="book" size={19} /> 语块卡</h2>
          <span className="badge">把一句建议变成能迁移的常用表达</span>
        </div>
        {err && <p className="error-banner" role="alert">{err}</p>}
        {cards === null && <p className="muted">正在加载…</p>}
        {cards !== null && cards.length === 0 && <p className="muted">还没有语块卡。回顾页每条建议旁点「语块卡」即可生成。</p>}
        {cards?.map((c) => (
          <article key={c.id} className="p-3 mt-3" style={{ background: "#f4f8f1", borderRadius: 8 }}>
            <div className="flex items-center justify-between">
              <span className="badge badge-neutral">语块卡 #{c.id}</span>
            </div>
            <p className="mt-2 text-sm muted">来源原句：{c.original_text}</p>
            <p className="mt-1"><strong>{c.suggestion_text}</strong></p>
            <p className="mt-2">主要语块：<mark>{c.chunk_text}</mark></p>
            <p className="mt-1 text-sm">中文解释：{c.explanation_zh}</p>
            <p className="mt-1 text-sm">适用情境：{c.context_zh}</p>
            <p className="mt-1 text-sm">可替换：{c.replaceable}</p>
            <p className="mt-1 text-sm">新例句：{c.example_text}</p>
          </article>
        ))}
      </section>
    </main>
  );
}
