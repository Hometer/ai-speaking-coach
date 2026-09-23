import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
}));

import NavBar from "../components/NavBar";

describe("NavBar", () => {
  it("渲染全部八个导航入口", () => {
    render(<NavBar />);
    for (const label of ["开始练习", "今日复练", "待复练", "语块卡", "进步中心", "七日计划", "练习历史", "学习记忆"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });
});
