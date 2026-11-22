import { useEffect, useRef, useState } from "react";
import type { SocketStatus } from "../lib/ws";
import type {
  ChatMessage,
  SessionReady,
  TranslationMode,
  TurnFeedback,
  TutorState,
} from "../types";
import MessageBubble from "./MessageBubble";
import PTTButton from "./PTTButton";

interface Props {
  session: SessionReady;
  messages: ChatMessage[];
  feedback: Record<number, TurnFeedback>;
  state: TutorState;
  busy: boolean;
  recording: boolean;
  status: SocketStatus;
  warmup: WarmupStatus | null;
  translation: TranslationMode;
  translating: Record<number, boolean>;
  /** Turns that already reached `turn.end`, so a missing translation can stop waiting. */
  endedTurns: Record<number, boolean>;
  onPttStart: () => void;
  onPttEnd: () => void;
  /** Returns false when the frame could not be sent, so the draft is kept. */
  onSendText: (text: string) => boolean;
  onExplain: (turnId: number) => void;
  onTranslate: (turnId: number) => void;
  onToggleTranslation: () => void;
  onEnd: () => void;
}

/** Advisory warm-up status of the local model; `null` renders no pill. */
export type WarmupStatus = "pending" | "done" | "failed";

const WARMUP_LABEL: Record<WarmupStatus, string> = {
  pending: "carregando modelo…",
  done: "modelo pronto",
  failed: "modelo indisponível",
};

const WARMUP_PILL: Record<WarmupStatus, string> = {
  pending: "bg-sky-500/10 text-sky-200 ring-1 ring-sky-500/30",
  done: "bg-emerald-500/10 text-emerald-200 ring-1 ring-emerald-500/30",
  failed: "bg-amber-500/10 text-amber-200 ring-1 ring-amber-500/30",
};

const STATE_LABEL: Record<TutorState, string> = {
  idle: "Pronto",
  listening: "Ouvindo",
  transcribing: "Transcrevendo",
  thinking: "Pensando",
  speaking: "Falando",
  feedback: "Corrigindo",
};

const STATE_DOT: Record<TutorState, string> = {
  idle: "bg-emerald-400",
  listening: "bg-rose-400",
  transcribing: "bg-sky-400",
  thinking: "bg-sky-400",
  speaking: "bg-violet-400",
  feedback: "bg-amber-400",
};

function ModelBadge({ label, value }: { label: string; value: string | null }) {
  return (
    <span className="rounded-full border border-slate-800 px-2 py-0.5 text-[10px] text-slate-400">
      {label}: {value ?? "—"}
    </span>
  );
}

export default function ChatScreen({
  session,
  messages,
  feedback,
  state,
  busy,
  recording,
  status,
  warmup,
  translation,
  translating,
  endedTurns,
  onPttStart,
  onPttEnd,
  onSendText,
  onExplain,
  onTranslate,
  onToggleTranslation,
  onEnd,
}: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  const [input, setInput] = useState("");
  const connected = status === "open";
  const free = session.mode === "free";

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, feedback]);

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-slate-800 bg-slate-950/90 px-4 py-3 backdrop-blur">
        <div className="mx-auto flex w-full max-w-4xl flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-[11px] uppercase tracking-wider text-slate-500">
              {session.language} · nível {session.level} · {session.level_scale}
            </p>
            <h2 className="text-sm font-semibold text-white">
              {free ? "Conversa livre" : session.scenario.title_pt}
            </h2>
            <p className="text-[11px] text-slate-500">
              {free ? "tema livre" : session.scenario.title}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={onToggleTranslation}
              aria-pressed={translation === "auto"}
              title="Tradução automática ou só no botão"
              className={`rounded-full border px-3 py-1 text-[11px] transition ${
                translation === "auto"
                  ? "border-sky-500/60 text-sky-200 hover:border-sky-400"
                  : "border-slate-700 text-slate-400 hover:border-slate-500"
              }`}
            >
              {translation === "auto" ? "PT auto" : "PT botão"}
            </button>
            <ModelBadge label="LLM" value={session.models.llm ?? null} />
            <ModelBadge label="STT" value={session.models.stt ?? null} />
            <ModelBadge label="TTS" value={session.models.tts ?? null} />
            <button
              type="button"
              onClick={onEnd}
              className="rounded-full border border-slate-700 px-3 py-1 text-[11px] text-slate-300 transition hover:border-rose-500 hover:text-rose-300"
            >
              Encerrar
            </button>
          </div>
        </div>
      </header>

      {!connected && (
        <div className="bg-rose-500/10 px-4 py-2 text-center text-xs text-rose-200">
          Conexão encerrada. Volte ao início para abrir uma nova sessão.
        </div>
      )}

      <main className="flex-1 overflow-y-auto px-4 py-6">
        <div className="mx-auto flex w-full max-w-4xl flex-col gap-4">
          {messages.length === 0 && (
            <p className="py-12 text-center text-sm text-slate-500">
              Comece falando sobre o que quiser
            </p>
          )}
          {messages.map((message) => (
            <MessageBubble
              key={message.id}
              message={message}
              language={session.language}
              translation={translation}
              translating={Boolean(translating[message.turnId])}
              turnEnded={Boolean(endedTurns[message.turnId])}
              feedback={message.role === "assistant" ? feedback[message.turnId] : undefined}
              onExplain={() => onExplain(message.turnId)}
              onTranslate={onTranslate}
            />
          ))}
          <div ref={endRef} />
        </div>
      </main>

      <footer className="border-t border-slate-800 bg-slate-950/95 px-4 py-4">
        <div className="mx-auto flex w-full max-w-4xl items-center gap-4">
          <div className="flex-1">
            <div className="mb-2 flex items-center gap-2 text-[11px] text-slate-400">
              <span className={`h-2 w-2 rounded-full ${STATE_DOT[state]}`} />
              {STATE_LABEL[state]}
              <span className="text-slate-600">·</span>
              <span>{session.models.llm}</span>
              {warmup && (
                <span className={`rounded-full px-2 py-0.5 text-[10px] ${WARMUP_PILL[warmup]}`}>
                  {WARMUP_LABEL[warmup]}
                </span>
              )}
            </div>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                const text = input.trim();
                if (!text) return;
                // Keep the draft when the socket is gone: the text is not lost.
                if (!onSendText(text)) return;
                setInput("");
              }}
            >
              <input
                value={input}
                onChange={(event) => setInput(event.target.value)}
                placeholder="Ou digite aqui em vez de falar…"
                className="w-full rounded-xl border border-slate-800 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none placeholder:text-slate-600 focus:border-sky-500"
              />
            </form>
            <p className="mt-2 text-[11px] text-slate-600">
              {free ? "Tema livre: escolha o assunto." : `Cenário: ${session.scenario.goal}`}
            </p>
          </div>
          <PTTButton
            recording={recording}
            disabled={!connected || busy}
            state={state}
            onStart={onPttStart}
            onEnd={onPttEnd}
          />
        </div>
      </footer>
    </div>
  );
}
