import { useCallback, useEffect, useRef, useState } from "react";
import ChatScreen, { type WarmupStatus } from "./components/ChatScreen";
import ExplainModal from "./components/ExplainModal";
import ReviewModal from "./components/ReviewModal";
import SetupScreen from "./components/SetupScreen";
import { getJson } from "./lib/api";
import { mergeAux } from "./lib/aids";
import {
  MicrophoneRecorder,
  RecordingCancelledError,
  revokeAudioUrls,
  tryWavBase64ToUrl,
} from "./lib/audio";
import { OrderedAudioQueue } from "./lib/audioQueue";
import { TutorSocket, type SocketStatus } from "./lib/ws";
import type {
  AppConfigPayload,
  ChatMessage,
  ExplainResult,
  Language,
  Review,
  Scenario,
  ServerEvent,
  SessionMode,
  SessionReady,
  TranslationMode,
  TurnFeedback,
  TutorState,
} from "./types";

interface ToastMessage {
  kind: "warning" | "error";
  message: string;
}

const BUSY_RESET_CODES = new Set([
  "busy",
  "audio_too_short",
  "empty_transcript",
  "no_session",
  "already_started",
]);

const SESSION_MODES = ["scenario", "free"] as const;
const TRANSLATION_MODES = ["auto", "manual"] as const;

/** Preferences survive a reload; a storage-less browser falls back to the defaults. */
const PREF_MODE = "ast.mode";
const PREF_TRANSLATION = "ast.translation";

/** A frame that could not be sent is never swallowed: the user sees why. */
const OFFLINE_TEXT = "Sem conexão: a mensagem não foi enviada.";
const OFFLINE_AUDIO = "Sem conexão: o áudio não foi enviado.";
const OFFLINE_REQUEST = "Sem conexão: o pedido não foi enviado.";
const EXPLAIN_MISMATCH = "Explicação de outro turno ignorada.";
const AUDIO_UNREADABLE = "Áudio do tutor ilegível neste trecho.";

/** Elements that own Space/Enter themselves; the push-to-talk shortcut yields. */
const INTERACTIVE_SELECTOR =
  'button, a[href], input, select, textarea, [role="button"], [contenteditable="true"]';

/** True when the focused element is a control that must keep its own keys. */
function focusedElementOwnsKeyboard(): boolean {
  const active = document.activeElement;
  return active instanceof Element && active.closest(INTERACTIVE_SELECTOR) !== null;
}

function readPreference<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const stored = window.localStorage.getItem(key);
    return allowed.find((item) => item === stored) ?? fallback;
  } catch {
    return fallback;
  }
}

function writePreference(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Private mode blocks storage; the preference just does not persist.
  }
}

function clearTurnFlag(flags: Record<number, boolean>, turnId: number): Record<number, boolean> {
  if (!flags[turnId]) return flags;
  const next = { ...flags };
  delete next[turnId];
  return next;
}

/** How long "modelo pronto" stays on screen before the pill disappears. */
const WARMUP_DONE_HOLD_MS = 1500;

function Toast({ toast }: { toast: ToastMessage }) {
  return (
    <div
      className={`fixed bottom-6 left-1/2 z-[60] -translate-x-1/2 rounded-xl px-4 py-2 text-xs shadow-xl ${
        toast.kind === "error"
          ? "bg-rose-500/15 text-rose-100 ring-1 ring-rose-500/40"
          : "bg-amber-500/15 text-amber-100 ring-1 ring-amber-500/40"
      }`}
    >
      {toast.message}
    </div>
  );
}

export default function App() {
  const [config, setConfig] = useState<AppConfigPayload | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);

  const [language, setLanguage] = useState<Language>("en");
  const [level, setLevel] = useState(3);
  const [profile, setProfile] = useState("");
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [scenarioId, setScenarioId] = useState("");
  const [mode, setMode] = useState<SessionMode>(() =>
    readPreference(PREF_MODE, SESSION_MODES, "scenario"),
  );
  const [translation, setTranslation] = useState<TranslationMode>(() =>
    readPreference(PREF_TRANSLATION, TRANSLATION_MODES, "auto"),
  );

  const [session, setSession] = useState<SessionReady | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [feedback, setFeedback] = useState<Record<number, TurnFeedback>>({});
  const [tutorState, setTutorState] = useState<TutorState>("idle");
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [status, setStatus] = useState<SocketStatus>("closed");
  const [warmup, setWarmup] = useState<WarmupStatus | null>(null);
  const [toast, setToast] = useState<ToastMessage | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [explain, setExplain] = useState<ExplainResult | null>(null);
  const [translating, setTranslating] = useState<Record<number, boolean>>({});
  /** Turns that already reached `turn.end`; a late translation may still land. */
  const [endedTurns, setEndedTurns] = useState<Record<number, boolean>>({});

  const socketRef = useRef<TutorSocket | null>(null);
  /** True between `connect()` and `session.end`; guards mid-session aid frames. */
  const sessionLiveRef = useRef(false);
  const recorderRef = useRef<MicrophoneRecorder | null>(null);
  /** True between the press and `recorder.start()` resolving. */
  const startingRef = useRef(false);
  /** A release/cancel that landed before the microphone finished opening. */
  const pendingRecordingActionRef = useRef<"stop" | "cancel" | null>(null);
  const queuesRef = useRef(new Map<number, OrderedAudioQueue>());
  /** Object URLs per turn, indexed by `seq`; owned here so they can be revoked. */
  const audioUrlsRef = useRef(new Map<number, string[]>());
  /** Turn the open explain modal was requested for. */
  const explainTurnRef = useRef<number | null>(null);
  const warmupTimerRef = useRef<number | null>(null);

  const clearWarmupTimer = useCallback(() => {
    if (warmupTimerRef.current === null) return;
    window.clearTimeout(warmupTimerRef.current);
    warmupTimerRef.current = null;
  }, []);

  const getQueue = useCallback((turnId: number) => {
    let queue = queuesRef.current.get(turnId);
    if (!queue) {
      queue = new OrderedAudioQueue();
      queuesRef.current.set(turnId, queue);
    }
    return queue;
  }, []);

  const revokeAllAudio = useCallback(() => {
    audioUrlsRef.current.forEach((urls) => revokeAudioUrls(urls));
    audioUrlsRef.current.clear();
  }, []);

  const rejectSend = useCallback((message: string) => {
    setToast({ kind: "error", message });
    setBusy(false);
  }, []);

  const handleEvent = useCallback(
    (event: ServerEvent) => {
      switch (event.type) {
        case "session.ready": {
          setSession(event);
          setMessages([]);
          setFeedback({});
          setTranslating({});
          setEndedTurns({});
          setTutorState("idle");
          setBusy(false);
          clearWarmupTimer();
          setWarmup(event.warmup === "pending" ? "pending" : null);
          // The object URLs of the previous thread die with it.
          revokeAllAudio();
          queuesRef.current.forEach((queue) => queue.reset());
          queuesRef.current.clear();
          // The server is the authority on the aids it actually enabled.
          const aidsTranslation = event.aids?.translation;
          if (aidsTranslation === "auto" || aidsTranslation === "manual") {
            setTranslation(aidsTranslation);
            writePreference(PREF_TRANSLATION, aidsTranslation);
          }
          if (event.warnings.length > 0) {
            setToast({ kind: "warning", message: event.warnings.join(" · ") });
          }
          break;
        }
        case "warmup.done":
          clearWarmupTimer();
          setWarmup("done");
          warmupTimerRef.current = window.setTimeout(() => {
            warmupTimerRef.current = null;
            setWarmup((current) => (current === "done" ? null : current));
          }, WARMUP_DONE_HOLD_MS);
          break;
        case "warmup.failed":
          clearWarmupTimer();
          setWarmup("failed");
          setToast({ kind: "warning", message: event.message });
          break;
        case "assistant.message":
          setMessages((prev) => [
            ...prev,
            {
              id: `assistant-${event.turn_id}`,
              turnId: event.turn_id,
              role: "assistant",
              text: event.text,
              opening: event.opening,
            },
          ]);
          break;
        case "stt.final":
          if (event.text.trim()) {
            setMessages((prev) => [
              ...prev,
              {
                id: `user-${event.turn_id}`,
                turnId: event.turn_id,
                role: "user",
                text: event.text,
              },
            ]);
          }
          break;
        case "llm.delta":
          setMessages((prev) => {
            const id = `assistant-${event.turn_id}`;
            const index = prev.findIndex((message) => message.id === id);
            if (index === -1) {
              return [
                ...prev,
                {
                  id,
                  turnId: event.turn_id,
                  role: "assistant",
                  text: event.text,
                },
              ];
            }
            const next = [...prev];
            next[index] = { ...next[index], text: next[index].text + event.text };
            return next;
          });
          break;
        case "tts.audio": {
          const url = tryWavBase64ToUrl(event.wav_b64);
          if (!url) {
            setToast({ kind: "warning", message: AUDIO_UNREADABLE });
            break;
          }
          const turnId = event.turn_id;
          const urls = audioUrlsRef.current.get(turnId) ?? [];
          const replaced = urls[event.seq];
          if (replaced && replaced !== url) revokeAudioUrls([replaced]);
          urls[event.seq] = url;
          audioUrlsRef.current.set(turnId, urls);
          const audioUrls = [...urls];
          setMessages((prev) => {
            const id = `assistant-${turnId}`;
            const index = prev.findIndex((message) => message.id === id);
            if (index === -1) {
              return [
                ...prev,
                {
                  id,
                  turnId,
                  role: "assistant",
                  text: "",
                  audioUrls,
                },
              ];
            }
            const next = [...prev];
            next[index] = { ...next[index], audioUrls };
            return next;
          });
          getQueue(turnId).push(event.seq, url);
          break;
        }
        case "assistant.aux":
          // Reading and translation may arrive in two frames, sometimes after `turn.end`.
          setMessages((prev) =>
            mergeAux(prev, event.turn_id, {
              reading: event.reading,
              translation_pt: event.translation_pt,
              translation_failed: event.translation_failed,
            }),
          );
          if (event.translation_pt) {
            setTranslating((prev) => clearTurnFlag(prev, event.turn_id));
          }
          break;
        case "translation.result":
          setMessages((prev) =>
            mergeAux(prev, event.turn_id, { translation_pt: event.translation_pt }),
          );
          setTranslating((prev) => clearTurnFlag(prev, event.turn_id));
          break;
        case "feedback.card":
          setFeedback((prev) => ({
            ...prev,
            [event.turn_id]: {
              corrections: event.corrections,
              vocab: event.vocab,
              goals: event.scene_progress.goals_met,
              languageLeak: event.language_leak,
            },
          }));
          break;
        case "state":
          setTutorState(event.state);
          break;
        case "turn.end":
          setBusy(false);
          setEndedTurns((prev) => ({ ...prev, [event.turn_id]: true }));
          break;
        case "warning":
          setToast({ kind: "warning", message: event.message });
          if (BUSY_RESET_CODES.has(event.code)) setBusy(false);
          // A failed on-demand translation must not leave the button stuck on "traduzindo…".
          if (event.code === "translation_failed") setTranslating({});
          break;
        case "error":
          setToast({ kind: "error", message: event.message });
          setBusy(false);
          explainTurnRef.current = null;
          break;
        case "explain.result": {
          const requested = explainTurnRef.current;
          if (requested !== null && event.turn_id !== requested) {
            // The answer belongs to another turn: showing it would explain the wrong bubble.
            setToast({ kind: "warning", message: EXPLAIN_MISMATCH });
            break;
          }
          explainTurnRef.current = null;
          setExplain(event);
          break;
        }
        case "review":
          setReview(event.review);
          break;
        case "pong":
          break;
      }
    },
    [getQueue, clearWarmupTimer, revokeAllAudio],
  );

  const handleRef = useRef(handleEvent);
  handleRef.current = handleEvent;

  useEffect(() => {
    getJson<AppConfigPayload>("/api/config")
      .then((data) => {
        setConfig(data);
        setProfile((current) => current || data.default_profile);
      })
      .catch((error: unknown) => {
        setBootError(error instanceof Error ? error.message : String(error));
      });
  }, []);

  useEffect(() => {
    let cancelled = false;
    getJson<{ items: Scenario[] }>(`/api/scenarios?language=${language}`)
      .then((data) => {
        if (cancelled) return;
        setScenarios(data.items);
        setScenarioId((current) =>
          data.items.some((item) => item.id === current)
            ? current
            : (data.items[0]?.id ?? ""),
        );
      })
      .catch((error: unknown) => {
        setBootError(error instanceof Error ? error.message : String(error));
      });
    return () => {
      cancelled = true;
    };
  }, [language]);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(timer);
  }, [toast]);

  const resetToSetup = useCallback(() => {
    socketRef.current?.close();
    socketRef.current = null;
    sessionLiveRef.current = false;
    recorderRef.current?.cancel();
    recorderRef.current = null;
    pendingRecordingActionRef.current = null;
    explainTurnRef.current = null;
    queuesRef.current.forEach((queue) => queue.reset());
    queuesRef.current.clear();
    revokeAllAudio();
    setRecording(false);
    setBusy(false);
    setSession(null);
    setMessages([]);
    setFeedback({});
    setTranslating({});
    setEndedTurns({});
    setTutorState("idle");
    setReview(null);
    setExplain(null);
    clearWarmupTimer();
    setWarmup(null);
  }, [clearWarmupTimer, revokeAllAudio]);

  const startSession = useCallback(() => {
    // Free mode has no scenario at all; a scenario session still needs one.
    if (mode === "scenario" && !scenarioId) return;
    setBootError(null);
    queuesRef.current.forEach((queue) => queue.reset());
    queuesRef.current.clear();
    revokeAllAudio();

    let client: TutorSocket | null = null;
    client = new TutorSocket({
      onEvent: (event) => handleRef.current(event),
      onStatus: setStatus,
      onOpen: () => {
        client?.send({
          type: "session.start",
          language,
          level,
          mode,
          ...(mode === "scenario" ? { scenario_id: scenarioId } : {}),
          profile: profile || undefined,
          aids: { translation, reading: true },
        });
      },
    });
    socketRef.current = client;
    sessionLiveRef.current = true;
    client.connect();
  }, [language, level, mode, scenarioId, profile, translation, revokeAllAudio]);

  /** Stops the recorder and closes the turn; used by both release paths. */
  const commitRecording = useCallback(
    async (recorder: MicrophoneRecorder) => {
      if (recorderRef.current === recorder) recorderRef.current = null;
      setRecording(false);
      const pcm = await recorder.stop();
      const socket = socketRef.current;
      if (!socket || !socket.sendBytes(pcm)) {
        rejectSend(OFFLINE_AUDIO);
        return;
      }
      setBusy(true);
      if (!socket.send({ type: "audio.end" })) rejectSend(OFFLINE_AUDIO);
    },
    [rejectSend],
  );

  const startRecording = useCallback(async () => {
    if (startingRef.current || recorderRef.current || busy || status !== "open") return;
    startingRef.current = true;
    pendingRecordingActionRef.current = null;
    const recorder = new MicrophoneRecorder();
    // The ref is set before the await, so a release during start can find it.
    recorderRef.current = recorder;
    try {
      await recorder.start();
      if (recorderRef.current !== recorder) {
        recorder.cancel();
        return;
      }
      const pending = pendingRecordingActionRef.current;
      pendingRecordingActionRef.current = null;
      if (pending === "cancel") {
        recorder.cancel();
        recorderRef.current = null;
        setRecording(false);
        return;
      }
      setRecording(true);
      const socket = socketRef.current;
      if (!socket || !socket.send({ type: "audio.start" })) {
        recorder.cancel();
        recorderRef.current = null;
        setRecording(false);
        rejectSend(OFFLINE_AUDIO);
        return;
      }
      // A quick tap released before the mic opened: close the turn right away.
      if (pending === "stop") await commitRecording(recorder);
    } catch (error: unknown) {
      if (recorderRef.current === recorder) recorderRef.current = null;
      setRecording(false);
      if (error instanceof RecordingCancelledError) return;
      setToast({
        kind: "error",
        message: error instanceof Error ? error.message : String(error),
      });
    } finally {
      startingRef.current = false;
    }
  }, [busy, status, commitRecording, rejectSend]);

  const stopRecording = useCallback(async () => {
    if (startingRef.current) {
      // The mic is still opening; start() closes the turn as soon as it can.
      pendingRecordingActionRef.current = pendingRecordingActionRef.current ?? "stop";
      return;
    }
    const recorder = recorderRef.current;
    if (!recorder) return;
    await commitRecording(recorder);
  }, [commitRecording]);

  const cancelRecording = useCallback(() => {
    if (startingRef.current) {
      pendingRecordingActionRef.current = "cancel";
      return;
    }
    const recorder = recorderRef.current;
    if (!recorder) return;
    recorder.cancel();
    recorderRef.current = null;
    setRecording(false);
    socketRef.current?.send({ type: "audio.end" });
  }, []);

  const sendText = useCallback(
    (text: string): boolean => {
      const socket = socketRef.current;
      if (!socket || !socket.send({ type: "text.input", text })) {
        // Keep the draft: the caller only clears the field when this returns true.
        rejectSend(OFFLINE_TEXT);
        return false;
      }
      setBusy(true);
      return true;
    },
    [rejectSend],
  );

  const requestExplain = useCallback(
    (turnId: number) => {
      const socket = socketRef.current;
      if (!socket || !socket.send({ type: "explain.request", turn_id: turnId, focus: "grammar" })) {
        rejectSend(OFFLINE_REQUEST);
        return;
      }
      explainTurnRef.current = turnId;
    },
    [rejectSend],
  );

  const chooseMode = useCallback((next: SessionMode) => {
    setMode(next);
    writePreference(PREF_MODE, next);
  }, []);

  const chooseTranslation = useCallback((next: TranslationMode) => {
    setTranslation(next);
    writePreference(PREF_TRANSLATION, next);
    // Before `session.start` the choice rides on the payload; mid-session it is a
    // frame. With a closed socket the local mirror is all there is to update.
    if (!sessionLiveRef.current) return;
    socketRef.current?.send({ type: "aids.update", translation: next });
  }, []);

  const toggleTranslation = useCallback(() => {
    chooseTranslation(translation === "auto" ? "manual" : "auto");
  }, [translation, chooseTranslation]);

  const requestTranslation = useCallback(
    (turnId: number) => {
      if (!sessionLiveRef.current || !socketRef.current) return;
      setTranslating((prev) => ({ ...prev, [turnId]: true }));
      if (!socketRef.current.send({ type: "translate.request", turn_id: turnId })) {
        setTranslating((prev) => clearTurnFlag(prev, turnId));
        rejectSend(OFFLINE_REQUEST);
      }
    },
    [rejectSend],
  );

  const endSession = useCallback(() => {
    sessionLiveRef.current = false;
    socketRef.current?.send({ type: "session.end" });
  }, []);

  useEffect(() => {
    if (!session) return;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        cancelRecording();
        return;
      }
      if (event.code !== "Space" || event.repeat) return;
      // Space belongs to a focused control; stealing it breaks buttons and fields.
      if (focusedElementOwnsKeyboard()) return;
      event.preventDefault();
      void startRecording();
    };
    const onKeyUp = (event: KeyboardEvent) => {
      if (event.code !== "Space") return;
      // No focus guard here: if a field took focus during the hold, the release
      // still has to close the microphone.
      void stopRecording();
    };
    // Losing the window (alt-tab, tab switch) must not leave the mic open.
    const onWindowBlur = () => cancelRecording();
    const onVisibilityChange = () => {
      if (document.hidden) cancelRecording();
    };

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onWindowBlur);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onWindowBlur);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [session, startRecording, stopRecording, cancelRecording]);

  useEffect(
    () => () => {
      socketRef.current?.close();
      recorderRef.current?.cancel();
      revokeAllAudio();
      clearWarmupTimer();
    },
    [clearWarmupTimer, revokeAllAudio],
  );

  if (!session) {
    return (
      <div className="min-h-full">
        {bootError && (
          <div className="mx-auto mt-6 w-full max-w-5xl px-6">
            <div className="rounded-xl bg-rose-500/10 px-4 py-3 text-xs text-rose-200 ring-1 ring-rose-500/30">
              Falha ao carregar a API: {bootError}
            </div>
          </div>
        )}
        <SetupScreen
          config={config}
          language={language}
          onLanguage={setLanguage}
          level={level}
          onLevel={setLevel}
          profile={profile}
          onProfile={setProfile}
          mode={mode}
          onMode={chooseMode}
          translation={translation}
          onTranslation={chooseTranslation}
          scenarios={scenarios}
          scenarioId={scenarioId}
          onScenario={setScenarioId}
          onStart={startSession}
          status={status}
        />
        {toast && <Toast toast={toast} />}
      </div>
    );
  }

  return (
    <div className="h-full">
      <ChatScreen
        session={session}
        messages={messages}
        feedback={feedback}
        state={tutorState}
        busy={busy}
        recording={recording}
        status={status}
        warmup={warmup}
        translation={translation}
        translating={translating}
        endedTurns={endedTurns}
        onPttStart={() => void startRecording()}
        onPttEnd={() => void stopRecording()}
        onSendText={sendText}
        onExplain={requestExplain}
        onTranslate={requestTranslation}
        onToggleTranslation={toggleTranslation}
        onEnd={endSession}
      />
      {review && <ReviewModal review={review} onClose={resetToSetup} />}
      {explain && <ExplainModal result={explain} onClose={() => setExplain(null)} />}
      {toast && <Toast toast={toast} />}
      {status === "closed" && (
        <button
          type="button"
          onClick={resetToSetup}
          className="fixed bottom-6 right-6 z-40 rounded-full bg-slate-800 px-4 py-2 text-xs text-slate-200 ring-1 ring-slate-700 hover:bg-slate-700"
        >
          Voltar ao início
        </button>
      )}
    </div>
  );
}
