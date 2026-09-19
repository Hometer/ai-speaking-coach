import type { Metadata } from "next";
import NavBar from "@/components/NavBar";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI 口语练习",
  description: "英文面试自我介绍口语练习",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="min-h-full flex flex-col">
        <NavBar />
        <div className="flex-1">{children}</div>
      </body>
    </html>
  );
}
