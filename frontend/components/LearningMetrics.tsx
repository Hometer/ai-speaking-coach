import type { LearningMetrics as Metrics } from "@/lib/types";
import styles from "./LearningMetrics.module.css";

export default function LearningMetrics({data}:{data:Metrics}) {
  return <section className="panel panel-padding mb-5" aria-label="产品指标与样本口径">
    <h2>产品指标与样本口径</h2><p className="text-xs muted mt-2">{new Date(data.window.start).toLocaleDateString("zh-CN")} 至 {new Date(data.window.end).toLocaleDateString("zh-CN")}。新采集动作不回填历史；比例仅描述样本，真实目标仍待内测验收。</p>
    <div className={styles.grid}>{Object.entries(data.metrics).map(([key,m])=><article className={styles.card} key={key}>
      <h3>{m.label}</h3><strong>{m.rate===null?"暂无可计算样本":`${(m.rate*100).toFixed(1)}%`}</strong>
      <p>分子 {m.numerator} / 分母 {m.denominator}{m.unknown>0?`；未知或待观察 ${m.unknown}`:""}</p>
      <details><summary>查看统计口径</summary><p>{m.basis}</p></details>
    </article>)}</div>
    <p className="text-xs muted mt-3">恢复待处理 {data.counts.recovery_pending ?? 0}；恢复失败 {data.counts.recovery_failed ?? 0}；结果待核对 {data.counts.recovery_unresolved ?? 0}。文字与提示完成另列，不能当作口语独立。</p>
  </section>;
}
