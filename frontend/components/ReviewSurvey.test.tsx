import { cleanup,fireEvent,render,screen,waitFor } from "@testing-library/react";
import { afterEach,expect,it,vi } from "vitest";
import ReviewSurvey from "./ReviewSurvey";
import { reviewSurvey } from "@/lib/api";
vi.mock("@/lib/api",()=>({reviewSurvey:vi.fn(),recordProductEvent:vi.fn()}));
afterEach(()=>{cleanup();vi.resetAllMocks();});

it("records exposure without inventing an answer and lets uncertain change to helpful",async()=>{
  vi.mocked(reviewSurvey).mockImplementation(async(_id,answer)=>({answer:answer==="shown"?null:answer,version:1}));
  render(<ReviewSurvey sessionId="s"/>);
  await waitFor(()=>expect(reviewSurvey).toHaveBeenCalledWith("s","shown"));
  expect(screen.queryByText("已记录，可以修改。")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button",{name:"暂时无法判断"}));
  await screen.findByText("已记录，可以修改。");
  expect(screen.getByRole("button",{name:"暂时无法判断"})).toHaveAttribute("aria-pressed","true");
  fireEvent.click(screen.getByRole("button",{name:"有帮助"}));
  await waitFor(()=>expect(screen.getByRole("button",{name:"有帮助"})).toHaveAttribute("aria-pressed","true"));
});

it("keeps the saved answer when a modification fails",async()=>{
  vi.mocked(reviewSurvey).mockResolvedValueOnce({answer:"helpful",version:2}).mockRejectedValueOnce(new Error("offline"));
  render(<ReviewSurvey sessionId="s"/>);
  await waitFor(()=>expect(screen.getByRole("button",{name:"有帮助"})).toHaveAttribute("aria-pressed","true"));
  fireEvent.click(screen.getByRole("button",{name:"帮助不大"}));
  await screen.findByRole("alert");
  expect(screen.getByRole("button",{name:"有帮助"})).toHaveAttribute("aria-pressed","true");
});
