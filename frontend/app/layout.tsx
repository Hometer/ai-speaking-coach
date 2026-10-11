import type { Metadata } from "next";
import NavBar from "@/components/NavBar";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI 口语教练｜情景对话与学习评估",
  description: "面向成人英语学习者，围绕职业与面试、旅行与生活、日常沟通设计情景练习。针对提示依赖、辅助作答与独立掌握容易混淆，以及练习后缺少持续复习的问题，构建情景对话、分层帮助、独立评估与复习计划相衔接的学习流程，帮助学习者逐步过渡到独立表达。",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="min-h-full flex flex-col">
        <a href="#main-content" className="skip-link">跳到主要内容</a>
        <NavBar />
        <div className="app-content" id="main-content" tabIndex={-1}>{children}</div>
      </body>
    </html>
  );
}
