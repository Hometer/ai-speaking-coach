"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import Icon, { type IconName } from "./Icon";

const items: { href: string; label: string; icon: IconName }[] = [
  { href: "/", label: "开始练习", icon: "home" },
  { href: "/daily-review", label: "今日复练", icon: "target" },
  { href: "/queue", label: "待复练", icon: "check" },
  { href: "/phrase-cards", label: "语块卡", icon: "book" },
  { href: "/progress", label: "进步中心", icon: "spark" },
  { href: "/plan", label: "七日计划", icon: "clock" },
  { href: "/history", label: "练习历史", icon: "history" },
  { href: "/memory", label: "学习记忆", icon: "book" },
];

export default function NavBar() {
  const pathname = usePathname();
  return (
    <aside className="sidebar">
      <Link href="/" className="brand-lockup" aria-label="AI 口语练习首页"><span className="brand-mark"><Icon name="mic" size={22} /></span><span>AI 口语练习<small>A LITTLE EVERY DAY</small></span></Link>
      <div className="nav-caption">我的学习空间</div>
      <nav aria-label="主导航" className="main-nav">
        {items.map((item) => {
          const active = item.href === "/" ? pathname === "/" || pathname.startsWith("/practice/") : pathname.startsWith(item.href);
          return <Link key={item.href} href={item.href} aria-current={active ? "page" : undefined} className={`nav-item ${active ? "active" : ""}`}><Icon name={item.icon} /><span>{item.label}</span>{active && <span className="nav-dot" />}</Link>;
        })}
      </nav>
      <div className="sidebar-note"><Icon name="leaf" size={27} /><p>每一次开口，<br />都离自信更近一点。</p><span>从今天的 5 分钟开始</span></div>
      <div className="sidebar-footer"><span className="avatar">我</span><div>个人练习空间<small>成人受邀内测</small></div><Icon name="shield" size={17} /></div>
    </aside>
  );
}
