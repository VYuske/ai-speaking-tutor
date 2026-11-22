import type { Review } from "../types";

interface Props {
  review: Review;
  onClose: () => void;
}

export default function ReviewModal({ review, onClose }: Props) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4">
      <div className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-slate-800 bg-slate-900 p-6 shadow-2xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs uppercase tracking-wider text-slate-500">Revisão</p>
            <h2 className="text-xl font-semibold text-white">Sessão concluída</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:border-slate-500"
          >
            Fechar
          </button>
        </div>

        <div className="mt-4 grid grid-cols-3 gap-3 text-center">
          <div className="rounded-xl bg-slate-950 p-3">
            <p className="text-2xl font-semibold text-white">{review.stats.turns}</p>
            <p className="text-[11px] text-slate-500">turnos</p>
          </div>
          <div className="rounded-xl bg-slate-950 p-3">
            <p className="text-2xl font-semibold text-white">{review.stats.corrections}</p>
            <p className="text-[11px] text-slate-500">correções</p>
          </div>
          <div className="rounded-xl bg-slate-950 p-3">
            <p className="text-2xl font-semibold text-white">{review.session.level}</p>
            <p className="text-[11px] text-slate-500">nível</p>
          </div>
        </div>

        {Object.keys(review.stats.by_type).length > 0 && (
          <div className="mt-4 flex flex-wrap gap-2">
            {Object.entries(review.stats.by_type).map(([type, count]) => (
              <span
                key={type}
                className="rounded-full bg-slate-950 px-2 py-1 text-[11px] text-slate-400 ring-1 ring-slate-800"
              >
                {type}: {count}
              </span>
            ))}
          </div>
        )}

        <div className="mt-6 space-y-4">
          {review.turns.map((turn) => (
            <div key={turn.idx} className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
              <p className="text-xs text-slate-500">Você</p>
              <p className="text-sm text-sky-200">{turn.user_text}</p>
              <p className="mt-2 text-xs text-slate-500">Tutor</p>
              <p className="text-sm text-slate-300">{turn.assistant_text}</p>
              {turn.corrections.length > 0 && (
                <ul className="mt-3 space-y-2">
                  {turn.corrections.map((correction, index) => (
                    <li key={index} className="text-xs text-slate-400">
                      <span className="line-through">{correction.original}</span>{" "}
                      <span className="text-emerald-300">{correction.corrected}</span>
                      <span className="block text-slate-500">{correction.explanation_pt}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
          {review.turns.length === 0 && (
            <p className="text-sm text-slate-500">Nenhum turno registrado.</p>
          )}
        </div>
      </div>
    </div>
  );
}
