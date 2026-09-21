import type { SVGProps } from "react";

const paths = {
  mic: "M12 15a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v7a3 3 0 0 0 3 3Zm-7-4v1a7 7 0 0 0 14 0v-1M12 19v3M8 22h8",
  home: "m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z",
  history: "M3 11a9 9 0 1 1 2.7 7.4M3 4v7h7M12 7v5l3 2",
  book: "M12 5c-3-2-6-2-9-1v16c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Zm0 0v16",
  arrow: "M4 12h16m-6-6 6 6-6 6",
  back: "M20 12H4m6-6-6 6 6 6",
  clock: "M12 8v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
  check: "m5 12 4 4L19 6",
  shield: "M12 3 3 7v5c0 5 9 9 9 9s9-4 9-9V7ZM8 12l3 3 5-6",
  spark: "m12 3 2.6 6.4L21 12l-6.4 2.6L12 21l-2.6-6.4L3 12l6.4-2.6ZM20 2v4m-2-2h4",
  chat: "M21 11a8 8 0 0 1-8 8H7l-5 3 2-6a8 8 0 0 1-1-5 8 8 0 0 1 8-8h2a8 8 0 0 1 8 8ZM8 11h.01M12 11h.01M16 11h.01",
  briefcase: "M9 6V4h6v2M3 7h18v14H3ZM3 12c6 3 12 3 18 0M10 12h4v4h-4Z",
  target: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM17 12a5 5 0 1 1-10 0 5 5 0 0 1 10 0ZM13 12a1 1 0 1 1-2 0 1 1 0 0 1 2 0Z",
  headphones: "M3 14v-3a9 9 0 0 1 18 0v3M3 12h4v8H3ZM17 12h4v8h-4Z",
  help: "M9 9a3 3 0 0 1 6 0c0 2-3 2-3 4m0 3h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
  send: "m22 2-7 20-4-9L2 9Zm0 0L11 13",
  stop: "M6 6h12v12H6Z",
  pause: "M8 4v16M16 4v16",
  play: "m8 4 12 8-12 8Z",
  trash: "M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7",
  close: "m6 6 12 12M6 18 18 6",
  logout: "M9 3H4v18h5m6-14 5 5-5 5M9 12h11",
  chevron: "m9 5 7 7-7 7",
  leaf: "M20 3C7 2 2 8 5 15s15 5 15-12ZM4 21 15 10",
} as const;

export type IconName = keyof typeof paths;
export default function Icon({ name, size = 20, ...props }: SVGProps<SVGSVGElement> & { name: IconName; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}><path d={paths[name]} /></svg>;
}
