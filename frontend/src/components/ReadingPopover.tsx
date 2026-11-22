import { useEffect, useRef, type ReactNode } from "react";
import { expectsReading, readingView, translationView } from "../lib/aids";
import type { AssistantAux, Language, TranslationMode } from "../types";

interface Props {
  language: Language;
  aux?: AssistantAux;
  translation: TranslationMode;
  translating: boolean;
  turnEnded: boolean;
  onTranslate: () => void;
  onClose: () => void;
}

function Line({ label, children }: { label: string; children: ReactNode }) {
  return (
    <p className="flex gap-1.5 leading-relaxed">
      <span className="shrink-0 text-slate-500">{label}</span>
      <span className="select-all text-slate-100">{children}</span>
    </p>
  );
}

/**
 * Reading and translation of one tutor message. It is a plain overlay: it never
 * touches the audio player, so opening it does not pause the reply.
 */
export default function ReadingPopover({
  language,
  aux,
  translation,
  translating,
  turnEnded,
  onTranslate,
  onClose,
}: Props) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    panelRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      const panel = panelRef.current;
      // The trigger lives next to the panel and toggles it itself.
      if (panel?.contains(target) || panel?.parentElement?.contains(target)) return;
      onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("mousedown", onPointerDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("mousedown", onPointerDown);
    };
  }, [onClose]);

  const reading = readingView(aux, language);
  const showsReading = expectsReading(language);
  const translationState = translationView(aux, translation, translating, turnEnded);

  return (
    <div
      ref={panelRef}
      role="dialog"
      aria-label="Leitura e tradução"
      tabIndex={-1}
      className="absolute left-0 top-full z-30 mt-1 w-72 space-y-1.5 rounded-xl border border-slate-700 bg-slate-950 p-3 text-left text-xs shadow-xl outline-none focus-visible:border-sky-500"
    >
      {showsReading &&
        (aux?.reading ? (
          <>
            {reading.kana && <Line label="Kana">{reading.kana}</Line>}
            {reading.phoneticLabel && reading.phonetic && (
              <Line label={reading.phoneticLabel}>{reading.phonetic}</Line>
            )}
          </>
        ) : (
          <p className="text-slate-500">carregando leitura…</p>
        ))}

      {translationState.kind === "ready" && <Line label="Tradução">{translationState.text}</Line>}

      {translationState.kind === "pending" && <Line label="Tradução">traduzindo…</Line>}

      {translationState.kind === "waiting" && (
        <Line label="Tradução">carregando tradução…</Line>
      )}

      {translationState.kind === "button" && (
        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            onClick={onTranslate}
            className="rounded-full border border-slate-700 px-2.5 py-0.5 text-slate-300 transition hover:border-sky-500 hover:text-sky-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-sky-400"
          >
            Traduzir
          </button>
          <span className="text-[10px] text-slate-500">tradução sob demanda</span>
        </div>
      )}
    </div>
  );
}
