"use client";

import { useEffect, useRef, useState } from "react";
import { fetchAudio, recordProductEvent } from "@/lib/api";

export default function AudioPlayer({ src, rate = 1, kind = "demo_play", autoPlay = false }: { src: string; rate?: number; autoPlay?: boolean; kind?: "demo_play" | "recording_play" }) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [url, setUrl] = useState("");
  const [failedSrc, setFailedSrc] = useState("");

  useEffect(() => {
    let active = true;
    const audio = audioRef.current;
    let objectUrl = "";
    fetchAudio(src)
      .then((u) => {
        if (!active) { URL.revokeObjectURL(u); return; }
        objectUrl = u;
        setUrl(u);
      })
      .catch(() => { if (active) setFailedSrc(src); });
    return () => {
      active = false;
      audio?.pause();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [src]);

  useEffect(() => {
    const audio = audioRef.current;
    return () => { audio?.pause(); };
  }, [url]);

  if (failedSrc === src) return <p className="text-xs muted mt-2" role="status">音频暂不可用或已清理，可继续阅读文字。</p>;
  if (!url) return <p className="text-xs muted mt-2" role="status">正在加载语音…</p>;
  return <audio ref={audioRef} autoPlay={autoPlay} controls preload="metadata" src={url} className="w-full mt-3" aria-label={kind === "recording_play" ? "本人录音" : "教练语音"} onError={() => setFailedSrc(src)} onPlay={e => { const sessionId = window.location.pathname.match(/^\/practice\/([^/]+)/)?.[1]; if (sessionId) recordProductEvent(sessionId, kind); (e.currentTarget as HTMLAudioElement).playbackRate = rate; document.querySelectorAll("audio").forEach(audio => { if (audio !== e.currentTarget) audio.pause(); }); }} />;
}
