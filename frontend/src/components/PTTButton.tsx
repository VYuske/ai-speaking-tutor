import type { KeyboardEvent } from "react";
import type { TutorState } from "../types";

const LABEL: Record<TutorState, string> = {
  idle: "Segure para falar",
  listening: "Ouvindo…",
  transcribing: "Transcrevendo…",
  thinking: "Pensando…",
  speaking: "Falando…",
  feedback: "Corrigindo…",
};

interface Props {
  recording: boolean;
  disabled: boolean;
  state: TutorState;
  onStart: () => void;
  onEnd: () => void;
}

export default function PTTButton({ recording, disabled, state, onStart, onEnd }: Props) {
  const label = recording ? "Solte para enviar" : LABEL[state];

  /** Space/Enter are owned by the button, so the window shortcut stays out of it. */
  const isActivationKey = (event: KeyboardEvent<HTMLButtonElement>) =>
    event.key === " " || event.key === "Enter";

  return (
    <button
      type="button"
      disabled={disabled}
      onPointerDown={(event) => {
        event.currentTarget.setPointerCapture(event.pointerId);
        onStart();
      }}
      onPointerUp={(event) => {
        event.currentTarget.releasePointerCapture(event.pointerId);
        onEnd();
      }}
      onPointerCancel={onEnd}
      onKeyDown={(event) => {
        if (!isActivationKey(event)) return;
        event.preventDefault();
        if (event.repeat) return;
        onStart();
      }}
      onKeyUp={(event) => {
        if (!isActivationKey(event)) return;
        event.preventDefault();
        onEnd();
      }}
      onBlur={onEnd}
      className={`relative flex h-28 w-28 touch-none select-none flex-col items-center justify-center rounded-full border-4 text-center text-xs font-semibold transition ${
        recording
          ? "border-rose-400 bg-rose-500/20 text-rose-100"
          : "border-slate-700 bg-slate-900 text-slate-300 hover:border-slate-600 disabled:border-slate-800 disabled:text-slate-600"
      }`}
      aria-label={label}
    >
      {recording && (
        <span className="absolute inset-0 animate-ping rounded-full border-2 border-rose-400/60" />
      )}
      <span className="px-3 leading-tight">{label}</span>
      <span className="mt-1 text-[10px] font-normal text-slate-500">
        {recording ? "ou Esc" : "ou Espaço"}
      </span>
    </button>
  );
}
