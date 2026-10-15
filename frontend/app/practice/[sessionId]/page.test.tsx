import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import Page from "./page";
import { getSession, submitTurn, endSession } from "@/lib/api";

vi.mock("next/navigation",()=>({useParams:()=>({sessionId:"s"}),useRouter:()=>({push:vi.fn()})}));
vi.mock("@/components/AudioPlayer",()=>({default:()=>null}));
vi.mock("@/lib/api",()=>({getSession:vi.fn(),submitTurn:vi.fn(),endSession:vi.fn(),recordProductEvent:vi.fn()}));
const base={session_id:"s",status:"WAITING_INPUT",goal_text:"确认预订",scene_label:"酒店入住",review:null,source_invalid:false,qa_recording_consent:false,turns:[],drill:null};
afterEach(()=>{cleanup();vi.resetAllMocks();});

it("keeps end available during a request and ignores its late reply",async()=>{
  vi.mocked(getSession).mockResolvedValue(base as never);
  let release:(value:unknown)=>void=()=>{};
  vi.mocked(submitTurn).mockImplementation(()=>new Promise(resolve=>{release=resolve as never;}));
  vi.mocked(endSession).mockResolvedValue({review:{improvements:[]},summary:{}} as never);
  render(<Page/>);
  const input=await screen.findByPlaceholderText(/用英文写下你的回答/);
  fireEvent.change(input,{target:{value:"I have a reservation."}});
  fireEvent.click(screen.getByRole("button",{name:"发送"}));
  await screen.findByText("正在处理…");
  const end=screen.getByRole("button",{name:"结束练习"});
  expect(end).toBeEnabled();expect(screen.getByRole("button",{name:"暂停"})).toBeEnabled();
  fireEvent.click(end);
  await waitFor(()=>expect(endSession).toHaveBeenCalledTimes(1));
  await screen.findByText("给这次练习，留一点回味。");
  await act(async()=>{release({status:"WAITING_INPUT",reply_text:"LATE_REPLY",action_type:"follow_up",turn_id:"late"});});
  expect(screen.queryByText("LATE_REPLY")).not.toBeInTheDocument();
  expect(screen.getByText("给这次练习，留一点回味。")).toBeInTheDocument();
});

it("does not render full answer history in the free speaking stage",async()=>{
  vi.mocked(getSession).mockResolvedValue({...base,status:"PRACTICE",drill:{drill_stage:"free_speak",stage_label:"脱稿说",visible_help:"确认你的预订",drill_target:"",drill_chunk:"",completed:false},turns:[{input_type:"coach",reply_text:"FULL_REFERENCE_ANSWER",practice_sentence:"FULL_REFERENCE_ANSWER"}]} as never);
  render(<Page/>);
  await screen.findByText(/脱稿阶梯/);
  expect(screen.queryByText("FULL_REFERENCE_ANSWER")).not.toBeInTheDocument();
  expect(screen.getByRole("button",{name:/录音说出/})).toBeEnabled();
});
