import { render, screen } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: ReactNode }) => createElement("a", { href }, children),
}));

vi.mock("@/lib/api", () => ({
  listPhraseCards: vi.fn().mockResolvedValue([]),
  listQueue: vi.fn().mockResolvedValue([]),
  addToQueue: vi.fn(),
  createPracticeSentence: vi.fn(),
}));

import PhraseCardsPage from "@/app/phrase-cards/page";

describe("PhraseCardsPage", () => {
  it("无数据时显示创建引导", async () => {
    render(<PhraseCardsPage />);
    expect(await screen.findByText(/还没有语块卡/)).toBeInTheDocument();
  });
});
