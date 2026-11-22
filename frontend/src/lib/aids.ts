import type { AssistantAux, ChatMessage, Language, Reading, TranslationMode } from "../types";

const READING_BADGE: Record<Language, string> = { ja: "あ", zh: "拼", en: "PT", de: "PT" };

/** Trigger label of the aid popover: `あ` (ja), `拼` (zh), `PT` (en/de). */
export function readingBadge(language: Language): { label: string; ariaLabel: string } {
  return { label: READING_BADGE[language], ariaLabel: "Leitura e tradução" };
}

/** Only japanese and chinese have a reading line; en/de get the translation alone. */
export function expectsReading(language: Language): boolean {
  return language === "ja" || language === "zh";
}

export interface AuxPatch {
  reading?: Reading;
  translation_pt?: string;
  translation_failed?: boolean;
}

/**
 * Merge one `assistant.aux` / `translation.result` frame into its turn.
 *
 * Reading and translation arrive in separate frames and may land after `turn.end`,
 * so the patch is merged into the previous `aux` instead of replacing it. A
 * translation that finally arrives clears a previous `translation_failed`. A turn
 * that is not in the thread is ignored: the arrays are returned untouched.
 */
export function mergeAux(messages: ChatMessage[], turnId: number, patch: AuxPatch): ChatMessage[] {
  if (!patch.reading && !patch.translation_pt && !patch.translation_failed) return messages;
  const index = messages.findIndex(
    (message) => message.turnId === turnId && message.role === "assistant",
  );
  if (index === -1) return messages;

  const current = messages[index];
  const aux: AssistantAux = { ...current.aux };
  if (patch.reading) aux.reading = patch.reading;
  if (patch.translation_pt) {
    aux.translation_pt = patch.translation_pt;
    delete aux.translation_failed;
  } else if (patch.translation_failed) {
    aux.translation_failed = true;
  }

  const next = [...messages];
  next[index] = { ...current, aux };
  return next;
}

export interface ReadingView {
  kana?: string;
  phoneticLabel?: "Romaji" | "Pinyin";
  phonetic?: string;
}

/** Lines of the reading section, already labelled for the target language. */
export function readingView(aux: AssistantAux | undefined, language: Language): ReadingView {
  const reading = aux?.reading;
  if (!reading || !expectsReading(language)) return {};
  if (language === "ja") {
    return {
      kana: reading.kana ?? undefined,
      phoneticLabel: "Romaji",
      phonetic: reading.text,
    };
  }
  return { phoneticLabel: "Pinyin", phonetic: reading.text };
}

export type TranslationView =
  | { kind: "ready"; text: string }
  | { kind: "button" }
  | { kind: "pending" }
  | { kind: "waiting" };

/**
 * What the translation section shows:
 * `button` = nothing to show yet and asking is possible (manual mode, a failed
 * automatic translation, or a turn that already ended without one);
 * `pending` = request in flight; `waiting` = automatic translation still on its
 * way. `turnEnded` is the escape hatch that keeps a lost translation from
 * leaving "carregando tradução…" on screen forever.
 */
export function translationView(
  aux: AssistantAux | undefined,
  translation: TranslationMode,
  translating: boolean,
  turnEnded = false,
): TranslationView {
  const text = aux?.translation_pt;
  if (text) return { kind: "ready", text };
  if (aux?.translation_failed) return { kind: "button" };
  if (translation === "manual") return translating ? { kind: "pending" } : { kind: "button" };
  return turnEnded ? { kind: "button" } : { kind: "waiting" };
}
