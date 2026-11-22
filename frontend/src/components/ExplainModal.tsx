import { useEffect, useMemo } from "react";
import { tryWavBase64ToUrl } from "../lib/audio";
import type { ExplainResult } from "../types";

interface Props {
  result: ExplainResult;
  onClose: () => void;
}

export default function ExplainModal({ result, onClose }: Props) {
  // A mangled payload must degrade to "no player", never throw during render.
  const audioUrl = useMemo(() => tryWavBase64ToUrl(result.wav_b64), [result.wav_b64]);

  useEffect(() => {
    if (!audioUrl) return;
    return () => URL.revokeObjectURL(audioUrl);
  }, [audioUrl]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4">
      <div className="w-full max-w-lg rounded-2xl border border-slate-800 bg-slate-900 p-6 shadow-2xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs uppercase tracking-wider text-slate-500">Explicação</p>
            <h2 className="text-lg font-semibold text-white">Em português</h2>
            <p className="text-[11px] text-slate-500">Turno {result.turn_id}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:border-slate-500"
          >
            Fechar
          </button>
        </div>

        <p className="mt-4 text-sm leading-relaxed text-slate-300">
          {result.explanation_pt || "Sem explicação disponível."}
        </p>

        {result.example_target && (
          <div className="mt-4 rounded-xl border border-slate-800 bg-slate-950 p-4">
            <p className="text-sm text-slate-100">{result.example_target}</p>
            <p className="mt-1 text-xs text-slate-500">{result.example_pt}</p>
            {audioUrl && (
              <button
                type="button"
                onClick={() => void new Audio(audioUrl).play().catch(() => undefined)}
                className="mt-3 rounded-full border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:border-slate-500"
              >
                Ouvir exemplo
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
