"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import Icon from "@/components/Icon";
import { addToQueue, createPracticeSentence, listPhraseCards, listQueue } from "@/lib/api";
import type { PhraseCard } from "@/lib/types";
import styles from "./page.module.css";

export default function PhraseCardsPage() {
  const router = useRouter();
  const [cards, setCards] = useState<PhraseCard[] | null>(null);
  const [err, setErr] = useState("");
  const [pending, setPending] = useState("");
  const [added, setAdded] = useState<Set<number>>(new Set());

  useEffect(() => {
    let active = true;
    Promise.all([listPhraseCards(), listQueue()])
      .then(([cardData, queueData]) => {
        if (!active) return;
        const queuedTurns = new Set(queueData.filter((item) => item.status !== "invalid").map((item) => item.source_turn_id));
        setCards(cardData);
        setAdded(new Set(cardData.filter((card) => queuedTurns.has(card.source_turn_id)).map((card) => card.id)));
      })
      .catch((e) => { if (active) setErr(e instanceof Error ? e.message : String(e)); });
    return () => { active = false; };
  }, []);

  async function practice(card: PhraseCard) {
    setPending(`practice-${card.id}`);
    setErr("");
    try {
      const session = await createPracticeSentence(card.source_session_id, card.source_turn_id);
      router.push(`/practice/${session.session_id}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setPending("");
    }
  }

  async function queue(card: PhraseCard) {
    setPending(`queue-${card.id}`);
    setErr("");
    try {
      await addToQueue(card.source_session_id, card.source_turn_id);
      setAdded((previous) => new Set(previous).add(card.id));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setPending("");
    }
  }

  return (
    <main className="page">
      <div className="topline">
        <span className="topline-title"><Icon name="book" size={15} /> 我的学习空间 / 语块卡</span>
        <Link href="/history" className="text-link">从练习回顾创建 <Icon name="arrow" size={14} /></Link>
      </div>
      <div className="eyebrow">PHRASE CARDS</div>
      <h1 className="page-title">把一句建议，变成随时能用的表达。</h1>
      <p className="page-description">保留来源和适用情境，换掉可替换的部分，再用新问题练一次迁移。</p>

      {err && <div className="error-banner" role="alert">{err}</div>}
      {cards === null && !err && (
        <div className={styles.cardGrid} role="status" aria-label="加载语块卡">
          <div className="skeleton" />
          <div className="skeleton" />
        </div>
      )}
      {cards !== null && cards.length === 0 && (
        <section className="panel empty-state">
          <span className="icon-tile"><Icon name="book" size={30} /></span>
          <h2>还没有语块卡</h2>
          <p>完成一段练习后，在回顾的表达建议旁点「语块卡」，就能把它保存成可迁移的练习材料。</p>
          <Link href="/history" className="btn btn-primary">查看练习回顾 <Icon name="arrow" size={15} /></Link>
        </section>
      )}

      {cards && cards.length > 0 && (
        <div className={`${styles.cardGrid} ${cards.length === 1 ? styles.singleCardGrid : ""}`} aria-live="polite">
          {cards.map((card, index) => (
            <article key={card.id} className={`panel ${styles.phraseCard}`}>
              <header className={styles.cardHeader}>
                <span className="badge badge-neutral">语块卡 {String(index + 1).padStart(2, "0")}</span>
                <span className={styles.sourceState}><Icon name="shield" size={13} /> 来源有效</span>
              </header>

              <div className={styles.sourceBlock}>
                <span>来源原句</span>
                <p>{card.original_text}</p>
              </div>

              <div className={styles.suggestionBlock}>
                <span>建议表达</span>
                <h2>{card.suggestion_text}</h2>
              </div>

              <div className={styles.chunkBlock}>
                <span>主要语块</span>
                <mark>{card.chunk_text}</mark>
              </div>

              <dl className={styles.detailGrid}>
                <div><dt>中文解释</dt><dd>{card.explanation_zh}</dd></div>
                <div><dt>适用情境</dt><dd>{card.context_zh}</dd></div>
                <div><dt>可替换部分</dt><dd>{card.replaceable}</dd></div>
                <div><dt>新例句</dt><dd>{card.example_text}</dd></div>
              </dl>

              <footer className={styles.cardActions}>
                <button onClick={() => practice(card)} disabled={!!pending} className="btn btn-primary">
                  <Icon name="mic" size={15} />{pending === `practice-${card.id}` ? "正在创建…" : "练这一句"}
                </button>
                <button onClick={() => queue(card)} disabled={!!pending || added.has(card.id)} className="btn btn-secondary">
                  <Icon name="check" size={15} />{added.has(card.id) ? "已加入待复练" : pending === `queue-${card.id}` ? "正在加入…" : "加入待复练"}
                </button>
              </footer>
            </article>
          ))}
        </div>
      )}

      <footer className="page-footer"><span><Icon name="shield" size={12} /> 语块卡随来源历史失效，不自动开启学习记忆</span><span>Learn the pattern. Make it yours.</span></footer>
    </main>
  );
}
