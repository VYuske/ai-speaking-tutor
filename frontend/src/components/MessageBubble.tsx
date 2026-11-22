import { useCallback, useRef, useState } from "react";
import { readingBadge } from "../lib/aids";
import { playInOrder } from "../lib/audioQueue";
import type { ChatMessage, Language, TranslationMode, TurnFeedback } from "../types";
import ReadingPopover from "./ReadingPopover";

const SEVERITY: Record<string, string> = {
  low: "bg-amber-400",
  medium: "bg-orange-400",
  high: "bg-rose-400",
};

interface Props {
  message: ChatMessage;
  language: Language;
  translation: TranslationMode;
  translating: boolean;
  turnEnded: boolean;
  feedback?: TurnFeedback;
  onExplain: () => void;
  onTranslate: (turnId: number) => void;
}

export default function MessageBubble({
  message,
  language,
  translation,
  translating,
  turnEnded,
  feedback,
  onExplain,
  onTranslate,
}: Props) {
  const isUser = message.role === "user";
  const isCjk = /[\u3040-\u30ff\u4e00-\u9fff]/.test(message.text);
  const audioUrls = message.audioUrls ?? [];
  const badge = readingBadge(language);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [popoverOpen, setPopoverOpen] = useState(false);

  // Stable so the popover's listeners and initial focus survive parent re-renders.
  const closePopover = useCallback(() => {
    setPopoverOpen(false);
    triggerRef.current?.focus();
  }, []);

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[85%] ${isUser ? "text-right" : "text-left"}`}>
        <div
          className={`rounded-2xl px-4 py-3 text-sm leading-relaxed ${
            isUser
              ? "rounded-br-md bg-sky-500/15 text-sky-50 ring-1 ring-sky-500/30"
              : "rounded-bl-md bg-slate-900 text-slate-100 ring-1 ring-slate-800"
          } ${isCjk ? "ja" : ""}`}
        >
          <p className="whitespace-pre-wrap">{message.text || "…"}</p>
        </div>

        <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
          {audioUrls.length > 0 && (
            <button
              type="button"
              onClick={() => playInOrder(audioUrls)}
              aria-label="Ouvir a resposta inteira"
              className="rounded-full border border-slate-800 px-2 py-0.5 text-slate-400 transition hover:border-slate-600 hover:text-slate-200"
            >
              Ouvir
            </button>
          )}
          {!isUser && (
            <span className="relative inline-flex">
              <button
                ref={triggerRef}
                type="button"
                aria-label={badge.ariaLabel}
                aria-expanded={popoverOpen}
                onClick={() => setPopoverOpen((open) => !open)}
                className="rounded-full border border-slate-800 px-2 py-0.5 text-slate-400 transition hover:border-slate-600 hover:text-slate-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-sky-400"
              >
                {badge.label}
              </button>
              {popoverOpen && (
                <ReadingPopover
                  language={language}
                  aux={message.aux}
                  translation={translation}
                  translating={translating}
                  turnEnded={turnEnded}
                  onTranslate={() => onTranslate(message.turnId)}
                  onClose={closePopover}
                />
              )}
            </span>
          )}
          {!isUser && !message.opening && (
            <button
              type="button"
              onClick={onExplain}
              className="rounded-full border border-slate-800 px-2 py-0.5 text-slate-400 transition hover:border-slate-600 hover:text-slate-200"
            >
              Explicar em PT
            </button>
          )}
          {message.opening && <span>abertura do cenário</span>}
        </div>

        {feedback && feedback.languageLeak && (
          <p className="mt-2 rounded-xl bg-amber-500/10 px-3 py-2 text-left text-xs text-amber-200 ring-1 ring-amber-500/30">
            Você saiu do idioma-alvo neste turno.
          </p>
        )}

        {feedback && feedback.corrections.length > 0 && (
          <div className="mt-2 space-y-2 text-left">
            {feedback.corrections.map((correction, index) => (
              <div
                key={`${correction.original}-${index}`}
                className="rounded-xl border border-slate-800 bg-slate-950/60 p-3"
              >
                <div className="flex items-center gap-2 text-xs">
                  <span
                    className={`h-1.5 w-1.5 rounded-full ${
                      SEVERITY[correction.severity] ?? "bg-slate-500"
                    }`}
                  />
                  <span className="text-slate-400">{correction.type}</span>
                </div>
                <p className="mt-1 text-xs text-slate-400 line-through">
                  {correction.original}
                </p>
                <p className="text-sm text-emerald-300">{correction.corrected}</p>
                <p className="mt-1 text-xs text-slate-400">{correction.explanation_pt}</p>
              </div>
            ))}
          </div>
        )}

        {feedback && feedback.vocab.length > 0 && (
          <div className="mt-2 flex flex-wrap justify-start gap-2">
            {feedback.vocab.map((item) => (
              <span
                key={item.term}
                className="rounded-full bg-slate-900 px-2 py-1 text-[11px] text-slate-300 ring-1 ring-slate-800"
              >
                {item.term} · {item.translation_pt}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
