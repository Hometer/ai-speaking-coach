import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import Page from "./page";
import { deletePlans } from "@/lib/api";
vi.mock("@/lib/api",()=>({getPlan:vi.fn(async()=>({id:1,goal_type:"workplace",status:"active",days:[]})),deletePlans:vi.fn(async()=>({deleted:2})),createPlan:vi.fn(),pausePlan:vi.fn(),restartPlan:vi.fn(),resumePlan:vi.fn(),skipPlanDay:vi.fn()}));
afterEach(()=>{cleanup();vi.clearAllMocks();vi.restoreAllMocks();});
it.each([false,true])("only deletes active and historical plans after confirmation=%s",async(confirmed)=>{
  const confirm=vi.spyOn(window,"confirm").mockReturnValue(confirmed);
  render(<Page/>);const button=await screen.findByRole("button",{name:"删除全部计划"});
  await waitFor(()=>expect(button).toBeEnabled());fireEvent.click(button);
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining("已有练习和待复练记录会保留"));
  if(confirmed)await waitFor(()=>expect(deletePlans).toHaveBeenCalledTimes(1));
  else expect(deletePlans).not.toHaveBeenCalled();
});
