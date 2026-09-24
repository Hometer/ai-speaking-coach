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
  audio_status?: "pending" | "ok" | "failed";
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
  recording_url: string;
  hint_used: boolean;
  eval_result: string;
  revised_text: string;
}

export interface DrillView {
  drill_stage: string;
  stage_label: string;
  drill_target: string;
  drill_meaning: string;
  drill_chunk: string;
  visible_help: string;
  completed: boolean;
  outcome: string;
}

export interface DrillAttemptResult extends DrillView {
  success: boolean;
  result: string;
  stage: string;
  next_stage: string;
}

export interface DueTodayItem {
  id: number;
  source_turn_id: string;
  suggestion_sentence: string;
  status: string;
  success_count: number;
  interval_days: number;
  due_at: string | null;
}

export interface DueToday {
  count: number;
  items: DueTodayItem[];
}

export interface DailyReviewSummary {
  session_id: string;
  status: string;
  completed: number;
  helped: number;
  skipped: number;
  deferred: number;
  total: number;
}

export interface DailyReviewCurrent {
  done: boolean;
  index: number;
  total: number;
  item?: DueTodayItem;
  summary?: DailyReviewSummary;
}

export interface DailyReviewAttemptResult {
  success: boolean;
  result: string;
  help_used: boolean;
  next: DailyReviewCurrent;
}

export interface PhraseCard {
  id: number;
  source_session_id: string;
  source_turn_id: string;
  original_text: string;
  suggestion_text: string;
  chunk_text: string;
  explanation_zh: string;
  context_zh: string;
  replaceable: string;
  example_text: string;
  status: string;
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
  drill: DrillView | null;
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

export interface QueueItem {
  id: number;
  source_session_id: string;
  source_turn_id: string;
  target_ability: string;
  suggestion_sentence: string;
  status: string;
  success_count: number;
  due_at: string | null;
  last_practiced_at: string | null;
  retest_result: string;
}

export interface Progress {
  completed_count: number;
  independent_rate: number;
  help_usage_rate: number;
  due_retest_done_rate: number;
  mastered_count: number;
  voice_evaluable_rate: number;
  drill_completed_count: number;
  transfer_completed_count: number;
  calc_version: string;
}

export interface PlanDay {
  day: number;
  scene_id: string;
  scene_label: string;
  status: string;
  retest_item_id: number | null;
  adjustment_reason: string;
}

export interface TrainingPlan {
  id: number;
  goal_type: string;
  days: PlanDay[];
  status: string;
}

export interface QaTurn {
  turn_id: string;
  input_type: string;
  user_text: string;
  reply_text: string;
  eval_result: string;
  help_type: string;
}

export interface QaSession {
  session_id: string;
  scene_id: string;
  status: string;
  started_at: string | null;
  turns: QaTurn[];
}
