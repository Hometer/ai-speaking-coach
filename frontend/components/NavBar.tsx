"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";
import Icon, { type IconName } from "./Icon";

const groups: { caption: string; items: { href: string; label: string; icon: IconName }[] }[] = [
  {
    caption: "练习",
    items: [{ href: "/", label: "开始练习", icon: "home" }],
  },
  {
    caption: "今日与复练",
    items: [
      { href: "/daily-review", label: "今日复练", icon: "target" },
      { href: "/queue", label: "待复练", icon: "check" },
      { href: "/plan", label: "七日计划", icon: "clock" },
    ],
  },
  {
    caption: "练习材料",
    items: [
      { href: "/phrase-cards", label: "语块卡", icon: "book" },
      { href: "/progress", label: "进步中心", icon: "spark" },
    ],
  },
  {
    caption: "记录",
    items: [
      { href: "/history", label: "练习历史", icon: "history" },
      { href: "/memory", label: "学习记忆", icon: "book" },
    ],
  },
];
export default function NavBar() {
  const pathname = usePathname();
  const navRef = useRef<HTMLElement>(null);

  useEffect(() => {
    navRef.current?.querySelector<HTMLElement>("[aria-current='page']")?.scrollIntoView({
      block: "nearest",
      inline: "center",
    });
  }, [pathname]);

  return (
    <aside className="sidebar">
      <Link href="/" className="brand-lockup" aria-label="AI 口语练习首页"><span className="brand-mark"><Icon name="mic" size={22} /></span><span>AI 口语练习<small>A LITTLE EVERY DAY</small></span></Link>
      <div className="nav-caption">我的学习空间</div>
      <nav ref={navRef} aria-label="主导航" className="main-nav">
        {groups.map((group) => (
          <div key={group.caption} className="nav-group">
            <div className="nav-group-caption">{group.caption}</div>
            {group.items.map((item) => {
              const active = item.href === "/" ? pathname === "/" || pathname.startsWith("/practice/") : pathname.startsWith(item.href);
              return <Link key={item.href} href={item.href} aria-current={active ? "page" : undefined} className={`nav-item ${active ? "active" : ""}`}><Icon name={item.icon} /><span>{item.label}</span>{active && <span className="nav-dot" />}</Link>;
            })}
          </div>
        ))}
      </nav>
      <div className="sidebar-note"><Icon name="leaf" size={27} /><p>每一次开口，<br />都离自信更近一点。</p><span>从今天的 5 分钟开始</span></div>
      <div className="sidebar-footer"><span className="avatar">我</span><div>个人练习空间<small>成人受邀内测</small></div><Icon name="shield" size={17} /></div>
    </aside>
  );
}
