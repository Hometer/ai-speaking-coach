export type Level = "beginner" | "basic" | "intermediate";
export type Difficulty = "simple" | "standard";
export type ActionType = "follow_up" | "hint" | "suggest_practice" | "recheck";
export type EvalResult = "independent" | "hinted" | "incomplete" | "insufficient_evidence";

export interface SessionCreated {
  session_id: string;
  status: string;
  goal_text: string;
  scene_id: string;
  scene_label: string;
  reply_text: string;
  reply_audio_url: string;
}

export interface TurnDone {
  turn_id: string;
  transcript: string;
  reply_text: string;
  reply_audio_url: string;
  action_type: ActionType;
  eval_result: EvalResult;
  status: string;
  hint_used: boolean;
  practice_sentence: string;
}

export interface TurnItem {
  turn_id: string;
  seq: number;
  input_type: string;
  user_text: string;
  transcript: string;
  reply_text: string;
  reply_audio_url: string;
  practice_sentence: string;
  action_type: string;
  hint_used: boolean;
  eval_result: string;
  revised_text: string;
}

export interface SessionDetail {
  session_id: string;
  status: string;
  goal_text: string;
  difficulty: string;
  end_reason: string | null;
  practice_type: string;
  scene_id: string;
  scene_label: string;
  source_invalid: boolean;
  review: Record<string, unknown> | null;
  turns: TurnItem[];
}

export interface SessionListItem {
  session_id: string;
  status: string;
  difficulty: string;
  started_at: string | null;
  ended_at: string | null;
  turn_count: number;
  goal_status: string;
  end_reason: string | null;
  scene_id: string;
  scene_label: string;
}

export interface MemoryItem {
  id: number;
  text: string;
  source_session_id: string;
  status: string;
  evidence_count: number;
}

export interface ApiErrorBody {
  error: { code: string; message: string };
}

export interface Scenario {
  scene_id: string;
  series: string;
  series_label: string;
  label: string;
  version: string;
  goal_zh: string;
  user_role: string;
  coach_role: string;
  goal_text?: string;
  levels?: string[];
  openings?: Record<string, string>;
}
