import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import Page from "./page";

vi.mock("@/lib/api", () => ({
  pendingVoiceRisk:vi.fn(async()=>null),
  dueToday: vi.fn(async () => ({count: 1, items: []})),
  startDailyReview: vi.fn(async () => ({session_id: "review"})),
  dailyReviewView: vi.fn(async () => ({done:false,index:0,total:1,item:{id:1,meaning_zh:"确认预订",suggestion_sentence:"",help_shown:false}})),
  getInviteCode: () => "fixture",
  intentKey: vi.fn(async()=>"intent"), clearIntentKey:vi.fn(), revealQueueItem:vi.fn(), recordProductEvent:vi.fn(),
}));
afterEach(() => { cleanup(); sessionStorage.clear(); vi.unstubAllGlobals(); });

it("allows stopping a recording and releases the microphone on submission", async () => {
  const stopTrack=vi.fn();
  class Recorder {
    static isTypeSupported() { return true; }
    state="inactive"; mimeType="audio/webm";
    ondataavailable?: (event:{data:Blob})=>void;
    onstop?: ()=>void;
    start() { this.state="recording"; }
    stop() { this.state="inactive"; this.ondataavailable?.({data:new Blob(["fixture"])}); this.onstop?.(); }
  }
  vi.stubGlobal("MediaRecorder",Recorder);
  Object.defineProperty(navigator,"mediaDevices",{configurable:true,value:{getUserMedia:vi.fn(async()=>({getTracks:()=>[{stop:stopTrack}]}))}});
  const send=vi.fn(async()=>({ok:true,json:async()=>({success:false,result:"pending",next:{done:false,index:0,total:1,item:{id:1,meaning_zh:"确认预订"}}})}));
  vi.stubGlobal("fetch",send);
  render(<Page/>);
  fireEvent.click(await screen.findByRole("button",{name:/开始今日复练/}));
  fireEvent.click(await screen.findByRole("button",{name:/录音回忆/}));
  const stop=await screen.findByRole("button",{name:/停止并提交/});
  expect(stop).toBeEnabled();
  fireEvent.click(stop);
  await waitFor(()=>expect(send).toHaveBeenCalledTimes(1));
  expect(stopTrack).toHaveBeenCalledTimes(1);
});

it("cancels capture on navigation without submitting audio", async () => {
  const stopTrack=vi.fn();const send=vi.fn();
  class Recorder { static isTypeSupported(){return true;} state="inactive"; onstop:(()=>void)|null=null; start(){this.state="recording";} stop(){this.state="inactive";this.onstop?.();} }
  vi.stubGlobal("MediaRecorder",Recorder);vi.stubGlobal("fetch",send);
  Object.defineProperty(navigator,"mediaDevices",{configurable:true,value:{getUserMedia:vi.fn(async()=>({getTracks:()=>[{stop:stopTrack}]}))}});
  const view=render(<Page/>);
  fireEvent.click(await screen.findByRole("button",{name:/开始今日复练/}));
  fireEvent.click(await screen.findByRole("button",{name:/录音回忆/}));
  await screen.findByRole("button",{name:/停止并提交/});view.unmount();
  expect(stopTrack).toHaveBeenCalled();expect(send).not.toHaveBeenCalled();
});
