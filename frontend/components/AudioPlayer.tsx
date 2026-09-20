"use client";

import { useEffect, useState } from "react";
import { fetchAudio } from "@/lib/api";

export default function AudioPlayer({ src }: { src: string }) {
  const [url, setUrl] = useState("");

  useEffect(() => {
    let active = true;
    let objectUrl = "";
    fetchAudio(src)
      .then((u) => {
        if (!active) return;
        objectUrl = u;
        setUrl(u);
      })
      .catch(() => {});
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [src]);

  if (!url) return null;
  return <audio controls src={url} className="w-full" />;
}
