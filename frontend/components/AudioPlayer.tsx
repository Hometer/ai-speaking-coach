"use client";

import { useEffect, useState } from "react";
import { fetchAudio } from "@/lib/api";

export default function AudioPlayer({ src }: { src: string }) {
  const [url, setUrl] = useState("");
  const [failedSrc, setFailedSrc] = useState("");

  useEffect(() => {
    let active = true;
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
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [src]);

  if (failedSrc === src) return <p className="text-xs muted mt-2" role="status">音频暂不可用或已清理，可继续阅读文字。</p>;
  if (!url) return <p className="text-xs muted mt-2" role="status">正在加载语音…</p>;
  return <audio controls preload="metadata" src={url} className="w-full mt-3" aria-label="教练语音" onError={() => setFailedSrc(src)} onPlay={e => { document.querySelectorAll("audio").forEach(audio => { if (audio !== e.currentTarget) audio.pause(); }); }} />;
}
