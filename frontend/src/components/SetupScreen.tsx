import type { AppConfigPayload, Language, Scenario, SessionMode, TranslationMode } from "../types";
import type { SocketStatus } from "../lib/ws";

const LANGUAGES: { code: Language; label: string; native: string }[] = [
  { code: "en", label: "Inglês", native: "English" },
  { code: "ja", label: "Japonês", native: "日本語" },
  { code: "zh", label: "Chinês", native: "中文" },
  { code: "de", label: "Alemão", native: "Deutsch" },
];

const LEVELS = [
  "Começando",
  "Básico",
  "Intermediário",
  "Intermediário alto",
  "Avançado",
  "Proficiente",
];

const MODES: { value: SessionMode; label: string; hint: string }[] = [
  { value: "scenario", label: "Cenário", hint: "Um papel e um objetivo fixos." },
  { value: "free", label: "Conversa livre", hint: "Você escolhe o assunto." },
];

const TRANSLATIONS: { value: TranslationMode; label: string; hint: string }[] = [
  { value: "auto", label: "Automática", hint: "Traduz cada resposta em background." },
  { value: "manual", label: "Desligada (botão)", hint: "Traduz só quando você pedir." },
];

interface Props {
  config: AppConfigPayload | null;
  language: Language;
  onLanguage: (language: Language) => void;
  level: number;
  onLevel: (level: number) => void;
  profile: string;
  onProfile: (profile: string) => void;
  mode: SessionMode;
  onMode: (mode: SessionMode) => void;
  translation: TranslationMode;
  onTranslation: (translation: TranslationMode) => void;
  scenarios: Scenario[];
  scenarioId: string;
  onScenario: (scenarioId: string) => void;
  onStart: () => void;
  status: SocketStatus;
}

export default function SetupScreen(props: Props) {
  const levelScale = props.config?.languages[props.language]?.level_scale ?? "CEFR";
  const free = props.mode === "free";
  const startDisabled = (!free && !props.scenarioId) || props.status === "connecting";
  // The scenario step disappears in free mode, so the following numbers shift up.
  const aidsStep = free ? 4 : 5;
  const modelsStep = free ? 5 : 6;

  return (
    <div className="mx-auto flex min-h-full w-full max-w-5xl flex-col gap-8 p-6 md:p-10">
      <header>
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-sky-400">
          Treino de fala local
        </p>
        <h1 className="mt-2 text-3xl font-semibold text-white md:text-4xl">
          AI Speaking Tutor
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-slate-400">
          Converse por voz, cumpra o objetivo do cenário e receba correções em português.
          Tudo roda na sua máquina.
        </p>
      </header>

      <section>
        <h2 className="mb-3 text-sm font-medium text-slate-300">1. Idioma</h2>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {LANGUAGES.map((item) => {
            const active = props.language === item.code;
            return (
              <button
                key={item.code}
                type="button"
                onClick={() => props.onLanguage(item.code)}
                className={`rounded-2xl border px-4 py-3 text-left transition ${
                  active
                    ? "border-sky-400 bg-sky-500/10 text-white"
                    : "border-slate-800 bg-slate-900/60 text-slate-300 hover:border-slate-700"
                }`}
              >
                <span className="block text-sm font-medium">{item.label}</span>
                <span className="mt-1 block text-xs text-slate-400">{item.native}</span>
              </button>
            );
          })}
        </div>
      </section>

      <section>
        <div className="mb-3 flex items-baseline justify-between">
          <h2 className="text-sm font-medium text-slate-300">2. Nível</h2>
          <span className="text-xs text-slate-500">escala {levelScale}</span>
        </div>
        <div className="flex flex-wrap gap-2">
          {LEVELS.map((label, index) => {
            const value = index + 1;
            const active = props.level === value;
            return (
              <button
                key={label}
                type="button"
                onClick={() => props.onLevel(value)}
                className={`rounded-full border px-3 py-1.5 text-xs transition ${
                  active
                    ? "border-emerald-400 bg-emerald-500/10 text-emerald-200"
                    : "border-slate-800 text-slate-400 hover:border-slate-700"
                }`}
              >
                {value} · {label}
              </button>
            );
          })}
        </div>
      </section>

      <section>
        <h2 className="mb-3 text-sm font-medium text-slate-300">3. Modo</h2>
        <div className="grid gap-3 md:grid-cols-2">
          {MODES.map((item) => {
            const active = props.mode === item.value;
            return (
              <button
                key={item.value}
                type="button"
                aria-pressed={active}
                onClick={() => props.onMode(item.value)}
                className={`rounded-2xl border p-4 text-left transition ${
                  active
                    ? "border-sky-400 bg-sky-500/10"
                    : "border-slate-800 bg-slate-900/60 hover:border-slate-700"
                }`}
              >
                <span className="block text-sm font-medium text-white">{item.label}</span>
                <span className="mt-1 block text-xs text-slate-400">{item.hint}</span>
              </button>
            );
          })}
        </div>
      </section>

      {!free && (
        <section>
          <div className="mb-3 flex items-baseline justify-between">
            <h2 className="text-sm font-medium text-slate-300">4. Cenário</h2>
            <span className="text-xs text-slate-500">
              {props.scenarios.length} disponíveis
            </span>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {props.scenarios.map((scenario) => {
              const active = props.scenarioId === scenario.id;
              return (
                <button
                  key={scenario.id}
                  type="button"
                  onClick={() => props.onScenario(scenario.id)}
                  className={`rounded-2xl border p-4 text-left transition ${
                    active
                      ? "border-sky-400 bg-sky-500/10"
                      : "border-slate-800 bg-slate-900/60 hover:border-slate-700"
                  }`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-sm font-medium text-white">
                      {scenario.title_pt}
                    </span>
                    <span className="rounded-full bg-slate-800 px-2 py-0.5 text-[10px] text-slate-400">
                      {scenario.level_min}–{scenario.level_max}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-slate-400">{scenario.title}</p>
                  <p className="mt-2 text-xs text-slate-500">{scenario.goal}</p>
                </button>
              );
            })}
            {props.scenarios.length === 0 && (
              <p className="text-sm text-slate-500">Nenhum cenário para este idioma.</p>
            )}
          </div>
        </section>
      )}

      <section>
        <h2 className="mb-3 text-sm font-medium text-slate-300">{aidsStep}. Ajudas</h2>
        <p className="mb-2 text-xs text-slate-500">
          Tradução em PT-BR da resposta do tutor.
        </p>
        <div className="flex flex-wrap gap-2">
          {TRANSLATIONS.map((item) => {
            const active = props.translation === item.value;
            return (
              <button
                key={item.value}
                type="button"
                aria-pressed={active}
                onClick={() => props.onTranslation(item.value)}
                title={item.hint}
                className={`rounded-full border px-3 py-1.5 text-xs transition ${
                  active
                    ? "border-emerald-400 bg-emerald-500/10 text-emerald-200"
                    : "border-slate-800 text-slate-400 hover:border-slate-700"
                }`}
              >
                {item.label}
              </button>
            );
          })}
        </div>
        <p className="mt-2 text-xs text-slate-500">
          Leitura em kana + romaji (japonês) e pinyin (chinês) vem junto da resposta, sem
          custo extra.
        </p>
      </section>

      <section>
        <h2 className="mb-3 text-sm font-medium text-slate-300">{modelsStep}. Modelos</h2>
        <label className="flex max-w-sm flex-col gap-1 text-xs text-slate-400">
          Perfil
          <select
            value={props.profile}
            onChange={(event) => props.onProfile(event.target.value)}
            className="rounded-xl border border-slate-800 bg-slate-900 px-3 py-2 text-sm text-slate-200 outline-none focus:border-sky-500"
          >
            {(props.config?.profiles ?? ["skeleton"]).map((profile) => (
              <option key={profile} value={profile}>
                {profile}
              </option>
            ))}
          </select>
        </label>
        <p className="mt-2 text-xs text-slate-500">
          O perfil define LLM, STT e TTS. Troque em{" "}
          <code className="rounded bg-slate-900 px-1">backend/config/models.toml</code>.
        </p>
      </section>

      <button
        type="button"
        onClick={props.onStart}
        disabled={startDisabled}
        className="mt-2 rounded-2xl bg-emerald-500 px-6 py-3 text-sm font-semibold text-slate-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:bg-slate-800 disabled:text-slate-500"
      >
        {props.status === "connecting" ? "Conectando…" : "Começar sessão"}
      </button>
    </div>
  );
}
