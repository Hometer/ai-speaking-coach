"use client";
import { useEffect, useRef, useState } from "react";
import { confirmVoiceRisk, discardVoiceRisk, pendingVoiceRisk } from "@/lib/api";
import type { RiskHold } from "@/lib/types";
import AudioPlayer from "./AudioPlayer";

export default function VoiceRisk({ scope, objectId, revision = 0, disabled = false, onResolved }: {
  scope: string; objectId: string; revision?: number; disabled?: boolean; onResolved: () => void | Promise<void>;
}) {
  const [risk, setRisk] = useState<RiskHold | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  useEffect(() => {
    const epoch = ++generation.current;
    void pendingVoiceRisk(scope, objectId).then(value => { if (epoch === generation.current) { setRisk(value); setText(value?.transcript || ""); } }).catch(() => {});
    return () => { generation.current = epoch + 1; };
  }, [scope, objectId, revision]);
  if (!risk?.risk_id) return null;
  async function resolve(discard: boolean) {
    const epoch = generation.current;
    setBusy(true); setError("");
    try {
      if (discard) await discardVoiceRisk(risk!.risk_id!);
      else await confirmVoiceRisk(risk!.risk_id!, text.trim());
      if (epoch !== generation.current) return;
      setRisk(null);
      await onResolved();
    } catch (e) { if (epoch === generation.current) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (epoch === generation.current) setBusy(false); }
  }
  return <section className="panel panel-padding mt-3" aria-label="核对复练转写">
    <h3>核对一下你说的话</h3><p className="text-sm muted">识别可能含杂音。确认前不会推进练习；录音过期后请重录，或丢弃后用文字回答。</p>
    {risk.recording_url && <AudioPlayer src={risk.recording_url} kind="recording_play" />}
    <label className="field-label">转写内容<textarea className="field" value={text} onChange={e => setText(e.target.value)} disabled={busy || disabled} /></label>
    {error && <p role="alert">{error}</p>}
    <div className="flex gap-2 flex-wrap mt-3"><button className="btn btn-primary" disabled={busy || disabled || !text.trim()} onClick={() => resolve(false)}>确认并继续</button><button className="btn btn-secondary" disabled={busy || disabled} onClick={() => resolve(true)}>丢弃，重新回答</button></div>
  </section>;
}
