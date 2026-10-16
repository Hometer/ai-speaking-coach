import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import VoiceRisk from "./VoiceRisk";
import { pendingVoiceRisk, confirmVoiceRisk, discardVoiceRisk } from "@/lib/api";
vi.mock("@/lib/api",()=>({pendingVoiceRisk:vi.fn(),confirmVoiceRisk:vi.fn(),discardVoiceRisk:vi.fn()}));
vi.mock("./AudioPlayer",()=>({default:()=>null}));
afterEach(()=>{cleanup();vi.resetAllMocks();});
it("keeps an expired risk visible for rerecord and never reports completion",async()=>{
  vi.mocked(pendingVoiceRisk).mockResolvedValue({risk_id:1,risk_hold:true,transcript:"unclear"} as never);
  vi.mocked(confirmVoiceRisk).mockRejectedValue(new Error("原录音已清理，请重新录音"));
  vi.mocked(discardVoiceRisk).mockResolvedValue({});const resolved=vi.fn();
  render(<VoiceRisk scope="drill_voice" objectId="s" onResolved={resolved}/>);
  fireEvent.click(await screen.findByRole("button",{name:"确认并继续"}));
  await screen.findByText("原录音已清理，请重新录音");expect(resolved).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button",{name:"丢弃，重新回答"}));
  await waitFor(()=>expect(resolved).toHaveBeenCalledTimes(1));
});
