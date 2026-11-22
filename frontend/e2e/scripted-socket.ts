import type { Page } from "@playwright/test";
import type {
  AssistantAux,
  Correction,
  Language,
  Scenario,
  ServerEvent,
  SessionMode,
  TranslationMode,
  VocabItem,
} from "../src/types";

/** Payload handed to `page.addInitScript`; must stay JSON-serializable. */
export interface ScriptedSocketPayload {
  wavB64: string;
  scenarios: Record<Language, Scenario>;
  script: ScriptedScript;
}

/** Everything the fake tutor says, so tests can assert on the same strings. */
export interface ScriptedScript {
  opening: string;
  replyChunks: string[];
  correction: Correction;
  vocab: VocabItem;
  goalsMet: string[];
  explain: {
    explanation_pt: string;
    example_target: string;
    example_pt: string;
  };
  /** Mocked `assistant.aux` per language: reading for ja/zh, translation for all. */
  aux: Record<Language, AssistantAux>;
}

/**
 * Message the scripted server sends when a test opts into a failed warm-up.
 * Duplicated inside `installScriptedSocket` because `page.addInitScript` only
 * serializes the function source, so module-scope bindings are not visible.
 */
export const WARMUP_FAILED_MESSAGE = "falha ao aquecer o modelo (e2e)";

interface ScriptedWindow extends Window {
  __tutorFrames?: Array<Record<string, unknown>>;
  /** Milliseconds between the opening turn and `warmup.done`; defaults to 0. */
  __warmupDelayMs?: number;
  /** When true, the scripted server answers the session with `warmup.failed`. */
  __warmupFailed?: boolean;
  /** Milliseconds before `translation.result`; defaults to one step. */
  __translateDelayMs?: number;
  /** When set, `assistant.aux` frames are held back by this many milliseconds. */
  __auxDelayMs?: number;
  /** Translation mode echoed by `session.ready.aids`, whatever the client asked. */
  __forceAidsTranslation?: TranslationMode;
  /** Answer `session.ready` with a null `models` map, to crash the header on render. */
  __brokenReady?: boolean;
  /** Answer the next turn with an `error` frame and no `turn.end`. */
  __errorOnTurn?: string;
  /** Emit one `tts.audio` per reply chunk instead of a single frame. */
  __replyInSentences?: boolean;
  /** Emit `assistant.aux {translation_failed: true}` instead of the automatic translation. */
  __translationFailed?: boolean;
  /** Close the socket right after the next completed turn. */
  __closeAfterTurn?: boolean;
  /** Added to the `turn_id` of every `explain.result`, to fake a mismatched answer. */
  __explainTurnOffset?: number;
  /** Answer `explain.result` with a payload that is not base64 at all. */
  __explainInvalidWav?: boolean;
  /** Answer `explain.result` with a playable wav, to exercise the modal player. */
  __explainAudio?: boolean;
}

/**
 * Installs a scripted stand-in for `window.WebSocket` before any app code runs
 * (`page.addInitScript`). The app only needs `send`, `close`, `readyState` and
 * the `onopen`/`onmessage`/`onclose` handlers (`frontend/src/lib/ws.ts`), so the
 * fake replays the server side of the protocol from `frontend/src/types.ts`.
 */
export function installScriptedSocket(payload: ScriptedSocketPayload): void {
  const { wavB64, scenarios, script } = payload;
  const frames: Array<Record<string, unknown>> = [];
  (window as ScriptedWindow).__tutorFrames = frames;

  // One macrotask per event: React renders each step, tests stay deterministic.
  const STEP_MS = 8;
  const SAMPLE_RATE = 8000;
  const DURATION_S = 0.02;
  // Mirrors `WARMUP_FAILED_MESSAGE` (module scope is invisible to init scripts).
  const WARMUP_FAILED = "falha ao aquecer o modelo (e2e)";

  const openings: Record<string, string> = {
    en: script.opening,
    ja: "いらっしゃいませ。ご注文は何ですか。",
    zh: "欢迎光临。您想点什么？",
    de: "Willkommen im Café. Was darf ich Ihnen bringen?",
  };

  class ScriptedWebSocket {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    static readonly CLOSING = 2;
    static readonly CLOSED = 3;

    readonly url: string;
    readyState = 0;
    onopen: ((event: { type: "open" }) => void) | null = null;
    onmessage: ((event: { data: string }) => void) | null = null;
    onclose: ((event: { type: "close"; code: number }) => void) | null = null;
    onerror: ((event: { type: "error" }) => void) | null = null;

    private turnId = 0;
    private language: Language = "en";
    private level = 3;
    private profile = "skeleton";
    private mode: SessionMode = "scenario";
    private translation: TranslationMode = "auto";
    private scenarioId = scenarios.en.id;
    private lastUserText = "";
    private lastReply = "";

    constructor(url: string) {
      this.url = url;
      window.setTimeout(() => {
        this.readyState = ScriptedWebSocket.OPEN;
        this.onopen?.({ type: "open" });
      }, 0);
    }

    send(data: unknown): void {
      if (typeof data !== "string") return; // binary audio frames are never scripted
      let frame: Record<string, unknown>;
      try {
        frame = JSON.parse(data) as Record<string, unknown>;
      } catch {
        return;
      }
      frames.push(frame);
      switch (frame.type) {
        case "session.start":
          this.startSession(frame);
          break;
        case "text.input":
          this.textInput(frame);
          break;
        case "session.end":
          this.endSession();
          break;
        case "explain.request":
          this.explain(frame);
          break;
        case "translate.request":
          this.translate(frame);
          break;
        case "aids.update":
          this.aidsUpdate(frame);
          break;
        case "audio.end":
          // Push-to-talk audio is binary and not scripted, but the turn still has
          // to close so the client does not stay busy waiting for `turn.end`.
          this.audioEnd();
          break;
        default:
          break; // audio.start / ping need no answer
      }
    }

    close(): void {
      if (this.readyState === ScriptedWebSocket.CLOSED) return;
      this.readyState = ScriptedWebSocket.CLOSED;
      this.onclose?.({ type: "close", code: 1000 });
    }

    private emit(event: ServerEvent, delayMs: number): void {
      window.setTimeout(() => {
        if (this.readyState !== ScriptedWebSocket.OPEN) return;
        this.onmessage?.({ data: JSON.stringify(event) });
      }, delayMs);
    }

    private emitAll(events: ServerEvent[]): void {
      events.forEach((event, index) => this.emit(event, (index + 1) * STEP_MS));
    }

    /**
     * Replays a turn in order, except when a test holds the aid frames back: then
     * every `assistant.aux` lands later, mimicking the real background translation.
     */
    private emitSequence(events: ServerEvent[]): void {
      const aidDelayMs = (window as ScriptedWindow).__auxDelayMs;
      if (aidDelayMs === undefined) {
        this.emitAll(events);
        return;
      }
      this.emitAll(events.filter((event) => event.type !== "assistant.aux"));
      events
        .filter((event) => event.type === "assistant.aux")
        .forEach((event) => this.emit(event, aidDelayMs));
    }

    private startSession(frame: Record<string, unknown>): void {
      this.language = (typeof frame.language === "string" ? frame.language : "en") as Language;
      this.level = typeof frame.level === "number" ? frame.level : 3;
      this.profile = typeof frame.profile === "string" ? frame.profile : "skeleton";
      this.mode = frame.mode === "free" ? "free" : "scenario";
      const aids = frame.aids;
      const forced = (window as ScriptedWindow).__forceAidsTranslation;
      this.translation =
        forced ??
        (typeof aids === "object" &&
        aids !== null &&
        (aids as Record<string, unknown>).translation === "manual"
          ? "manual"
          : "auto");

      const free = this.mode === "free";
      const base = scenarios[this.language] ?? scenarios.en;
      // Free mode has no scenario file: the backend sends a synthetic one (`id="free"`).
      const scenario: Scenario = free
        ? {
            ...base,
            id: "free",
            title: "Conversa livre",
            title_pt: "Conversa livre",
            goal: "Fale sobre o tema que você escolher.",
          }
        : base;
      this.scenarioId = free
        ? "free"
        : typeof frame.scenario_id === "string" && frame.scenario_id
          ? frame.scenario_id
          : scenario.id;
      this.turnId = 0;

      const opening = openings[this.language] ?? openings.en;
      const aux = script.aux[this.language] ?? script.aux.en;
      const events: ServerEvent[] = [
        {
          type: "session.ready",
          session_id: "e2e-session",
          language: this.language,
          level: this.level,
          profile: this.profile,
          level_scale: "CEFR",
          mode: this.mode,
          opening: !free,
          aids: { translation: this.translation, reading: true },
          scenario,
          // `__brokenReady` sends no model map at all, so the header throws on render.
          models: (window as ScriptedWindow).__brokenReady
            ? (null as unknown as Record<string, string | null>)
            : { llm: "e2e-llm", stt: "e2e-stt", tts: "e2e-tts" },
          warnings: [],
          warmup: "pending",
        },
      ];

      if (!free) {
        // The opening is turn 0 and carries its own aux, like the real runner.
        events.push(
          { type: "assistant.message", turn_id: 0, text: opening, opening: true },
          { type: "state", state: "speaking" },
          {
            type: "tts.audio",
            turn_id: 0,
            seq: 0,
            text: opening,
            source: "opening",
            wav_b64: wavB64,
            sample_rate: SAMPLE_RATE,
            duration_s: DURATION_S,
          },
          { type: "state", state: "idle" },
        );
        if (aux.reading) events.push({ type: "assistant.aux", turn_id: 0, reading: aux.reading });
        if (this.translation === "auto" && aux.translation_pt) {
          events.push({ type: "assistant.aux", turn_id: 0, translation_pt: aux.translation_pt });
        }
      } else {
        events.push({ type: "state", state: "idle" });
      }

      this.emitSequence(events);

      // Warm-up is answerable independently so tests can hold it pending.
      const warmupDelayMs = (window as ScriptedWindow).__warmupDelayMs ?? 0;
      const warmupAt = (events.length + 1) * STEP_MS + warmupDelayMs;
      if ((window as ScriptedWindow).__warmupFailed) {
        this.emit({ type: "warmup.failed", message: WARMUP_FAILED }, warmupAt);
      } else {
        this.emit({ type: "warmup.done", cold_start_ms: 42 }, warmupAt);
      }
    }

    private textInput(frame: Record<string, unknown>): void {
      this.turnId += 1;
      const turnId = this.turnId;
      const scripted = window as ScriptedWindow;
      const text = typeof frame.text === "string" ? frame.text : "";
      const reply = script.replyChunks.join("");
      this.lastUserText = text;
      this.lastReply = reply;

      const aux = script.aux[this.language] ?? script.aux.en;
      const deltas: ServerEvent[] = script.replyChunks.map((chunk) => ({
        type: "llm.delta",
        turn_id: turnId,
        text: chunk,
      }));

      const events: ServerEvent[] = [
        {
          type: "stt.final",
          turn_id: turnId,
          text,
          language: this.language,
          confidence: 0.95,
          source: "text",
          stt_ms: 8,
        },
        { type: "state", state: "thinking" },
        ...deltas,
      ];

      // A failed turn ends with `error` and never reaches `turn.end`.
      if (scripted.__errorOnTurn) {
        events.push({
          type: "error",
          code: "turn_failed",
          message: scripted.__errorOnTurn,
          recoverable: true,
        });
        this.emitSequence(events);
        return;
      }

      // The reading is a local lookup: it lands before `turn.end`.
      if (aux.reading) events.push({ type: "assistant.aux", turn_id: turnId, reading: aux.reading });
      events.push(
        ...this.replyAudio(turnId, reply),
        { type: "state", state: "speaking" },
        {
          type: "feedback.card",
          turn_id: turnId,
          corrections: [script.correction],
          vocab: [script.vocab],
          scene_progress: { goals_met: script.goalsMet, status: "ongoing" },
          language_leak: false,
        },
        { type: "state", state: "feedback" },
        { type: "state", state: "idle" },
        { type: "turn.end", turn_id: turnId, timings: { llm_ms: 120, tts_ms: 60 } },
      );
      // Automatic translation runs in the background and may land after `turn.end`.
      if (this.translation === "auto") {
        if (scripted.__translationFailed) {
          events.push({ type: "assistant.aux", turn_id: turnId, translation_failed: true });
        } else if (aux.translation_pt) {
          events.push({ type: "assistant.aux", turn_id: turnId, translation_pt: aux.translation_pt });
        }
      }

      this.emitSequence(events);

      if (scripted.__closeAfterTurn) {
        window.setTimeout(() => this.close(), (events.length + 3) * STEP_MS);
      }
    }

    /** One frame per sentence when `__replyInSentences` is set, one frame otherwise. */
    private replyAudio(turnId: number, reply: string): ServerEvent[] {
      const sentences = (window as ScriptedWindow).__replyInSentences
        ? script.replyChunks
        : [reply];
      return sentences.map((sentence, seq) => ({
        type: "tts.audio",
        turn_id: turnId,
        seq,
        text: sentence,
        source: "reply",
        wav_b64: wavB64,
        sample_rate: SAMPLE_RATE,
        duration_s: DURATION_S,
      }));
    }

    private endSession(): void {
      const hasTurn = this.turnId > 0;
      this.emit(
        {
          type: "review",
          review: {
            session: {
              id: "e2e-session",
              language: this.language,
              level: this.level,
              scenario_id: this.scenarioId,
              profile: this.profile,
              status: "ended",
              started_at: "2026-01-01T09:00:00+00:00",
              ended_at: "2026-01-01T09:05:00+00:00",
            },
            turns: hasTurn
              ? [
                  {
                    idx: this.turnId,
                    user_text: this.lastUserText,
                    assistant_text: this.lastReply,
                    corrections: [script.correction],
                    timings: { llm_ms: 120, tts_ms: 60 },
                  },
                ]
              : [],
            stats: {
              turns: this.turnId,
              corrections: hasTurn ? 1 : 0,
              by_type: hasTurn ? { [script.correction.type]: 1 } : {},
            },
          },
        },
        STEP_MS,
      );
    }

    /** `__explainTurnOffset` fakes a server that answers for the wrong turn. */
    private explain(frame: Record<string, unknown>): void {
      const scripted = window as ScriptedWindow;
      const requested = typeof frame.turn_id === "number" ? frame.turn_id : this.turnId;
      this.emit(
        {
          type: "explain.result",
          turn_id: requested + (scripted.__explainTurnOffset ?? 0),
          explanation_pt: script.explain.explanation_pt,
          example_target: script.explain.example_target,
          example_pt: script.explain.example_pt,
          wav_b64: scripted.__explainInvalidWav
            ? "%%%isto-nao-e-base64%%%"
            : scripted.__explainAudio
              ? wavB64
              : null,
        },
        STEP_MS,
      );
    }

    /** Answers the raw PCM of a push-to-talk turn with an empty closed turn. */
    private audioEnd(): void {
      this.turnId += 1;
      this.emit(
        { type: "turn.end", turn_id: this.turnId, timings: { stt_ms: 5 } },
        STEP_MS,
      );
    }

    /** Answers `translate.request` for the requested turn only. */
    private translate(frame: Record<string, unknown>): void {
      const turnId = typeof frame.turn_id === "number" ? frame.turn_id : this.turnId;
      const aux = script.aux[this.language] ?? script.aux.en;
      this.emit(
        {
          type: "translation.result",
          turn_id: turnId,
          translation_pt: aux.translation_pt ?? "",
        },
        (window as ScriptedWindow).__translateDelayMs ?? STEP_MS,
      );
    }

    /** Mid-session Auto/Desligada switch: applies to the following turns. */
    private aidsUpdate(frame: Record<string, unknown>): void {
      this.translation = frame.translation === "manual" ? "manual" : "auto";
    }
  }

  Object.defineProperty(window, "WebSocket", {
    configurable: true,
    writable: true,
    value: ScriptedWebSocket,
  });
}

/** Frames the app sent through the scripted socket, oldest first. */
export function getSentFrames(page: Page): Promise<Array<Record<string, unknown>>> {
  return page.evaluate(() => (window as ScriptedWindow).__tutorFrames ?? []);
}
