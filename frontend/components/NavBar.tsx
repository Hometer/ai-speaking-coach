"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const items = [
  { href: "/", label: "首页" },
  { href: "/history", label: "历史" },
  { href: "/memory", label: "记忆" },
];

export default function NavBar() {
  const pathname = usePathname();
  return (
    <nav className="max-w-2xl mx-auto flex items-center gap-2 px-4 py-3">
      {items.map((it) => {
        const active =
          it.href === "/" ? pathname === "/" : pathname.startsWith(it.href);
        return (
          <Link
            key={it.href}
            href={it.href}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium ${
              active ? "text-white" : ""
            }`}
            style={
              active
                ? { background: "var(--brand)" }
                : { color: "var(--muted)" }
            }
          >
            {it.label}
          </Link>
        );
      })}
    </nav>
  );
}
