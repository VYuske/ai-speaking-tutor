export type Language = "en" | "ja" | "zh" | "de";

export type TutorState =
  | "idle"
  | "listening"
  | "transcribing"
  | "thinking"
  | "speaking"
  | "feedback";

export interface Scenario {
  id: string;
  language: Language;
  title: string;
  title_pt: string;
  level_min: number;
  level_max: number;
  goal: string;
  setting: string;
}

export interface Correction {
  original: string;
  corrected: string;
  explanation_pt: string;
  type: string;
  severity: "low" | "medium" | "high";
  confidence: number;
}

export interface VocabItem {
  term: string;
  translation_pt: string;
}

export interface SceneProgress {
  goals_met: string[];
  status: "ongoing" | "complete";
}

export interface ReviewTurn {
  idx: number;
  user_text: string;
  assistant_text: string;
  corrections: Correction[];
  timings: Record<string, number | null>;
}

export interface Review {
  session: {
    id: string;
    language: Language;
    level: number;
    scenario_id: string;
    profile: string;
    status: string;
    started_at: string;
    ended_at: string | null;
  };
  turns: ReviewTurn[];
  stats: { turns: number; corrections: number; by_type: Record<string, number> };
}

export type SessionMode = "scenario" | "free";

export type TranslationMode = "auto" | "manual";

/** Learning aids of one session, echoed by `session.ready`. */
export interface Aids {
  translation: TranslationMode;
  reading: boolean;
}

export interface ReadingSegment {
  text: string;
  reading: string;
  romaji?: string | null;
}

export interface Reading {
  kind: "furigana" | "pinyin";
  text: string;
  kana?: string | null;
  segments?: ReadingSegment[];
}

/** Auxiliary material of one tutor turn; every field may arrive separately. */
export interface AssistantAux {
  reading?: Reading;
  translation_pt?: string;
  /** The automatic translation gave up; the popover must offer the manual button. */
  translation_failed?: boolean;
}

export interface ChatMessage {
  id: string;
  turnId: number;
  role: "user" | "assistant";
  text: string;
  /** Spoken sentences of a tutor turn, indexed by `tts.audio.seq`. */
  audioUrls?: string[];
  opening?: boolean;
  aux?: AssistantAux;
}

export interface SessionReady {
  session_id: string;
  language: Language;
  level: number;
  profile: string;
  level_scale: string;
  mode: SessionMode;
  /** False in free mode: no opening line is spoken. */
  opening: boolean;
  aids: Aids;
  scenario: Scenario;
  models: Record<string, string | null>;
  warnings: string[];
  warmup?: "pending";
  vram?: { total_mib: number | null; free_mib: number | null };
}

export interface TurnFeedback {
  corrections: Correction[];
  vocab: VocabItem[];
  goals: string[];
  languageLeak: boolean;
}

export interface ExplainResult {
  turn_id: number;
  explanation_pt: string;
  example_target: string;
  example_pt: string;
  wav_b64: string | null;
}

export type ServerEvent =
  | ({ type: "session.ready" } & SessionReady)
  | { type: "assistant.message"; turn_id: number; text: string; opening?: boolean }
  | ({
      type: "assistant.aux";
      turn_id: number;
    } & AssistantAux)
  | { type: "translation.result"; turn_id: number; translation_pt: string }
  | {
      type: "stt.final";
      turn_id: number;
      text: string;
      language: string;
      confidence: number | null;
      source: "audio" | "text";
      stt_ms: number;
    }
  | { type: "llm.delta"; turn_id: number; text: string }
  | {
      type: "tts.audio";
      turn_id: number;
      seq: number;
      text: string;
      source: "opening" | "reply";
      wav_b64: string;
      sample_rate: number;
      duration_s: number;
    }
  | {
      type: "feedback.card";
      turn_id: number;
      corrections: Correction[];
      vocab: VocabItem[];
      scene_progress: SceneProgress;
      language_leak: boolean;
    }
  | ({ type: "explain.result" } & ExplainResult)
  | { type: "warmup.done"; cold_start_ms: number }
  | { type: "warmup.failed"; message: string }
  | { type: "state"; state: TutorState }
  | { type: "warning"; code: string; message: string }
  | { type: "error"; code: string; message: string; recoverable: boolean }
  | { type: "review"; review: Review }
  | {
      type: "turn.end";
      turn_id: number;
      timings: Record<string, number | null>;
    }
  | { type: "pong" };

export interface AppConfigPayload {
  default_profile: string;
  profiles: string[];
  languages: Record<
    string,
    {
      profile: string;
      level_scale: string;
      llm: { model: string | null; endpoint: string | null };
      stt: { provider: string | null; device?: string };
      tts: { provider: string | null; device?: string };
    }
  >;
}
