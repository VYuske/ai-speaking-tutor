import type { Page } from "@playwright/test";
import type { AppConfigPayload, Language, Scenario } from "../src/types";
import { installScriptedSocket, type ScriptedSocketPayload } from "./scripted-socket";
import { SILENT_WAV_B64 } from "./silent-wav";

/** The sentence the tests type; also the input of the scripted correction. */
export const USER_TEXT = "I want a coffe";

/** One scenario per language, shaped like `Scenario` in `frontend/src/types.ts`. */
export const SCENARIOS: Record<Language, Scenario> = {
  en: {
    id: "cafe-en",
    language: "en",
    title: "Ordering at a café",
    title_pt: "Pedindo em um café",
    level_min: 1,
    level_max: 4,
    goal: "Peça uma bebida e confirme o tamanho.",
    setting: "café movimentado",
  },
  ja: {
    id: "cafe-ja",
    language: "ja",
    title: "カフェで注文する",
    title_pt: "Pedindo em um café (japonês)",
    level_min: 1,
    level_max: 4,
    goal: "飲み物とサイズを伝える。",
    setting: "カフェ",
  },
  zh: {
    id: "cafe-zh",
    language: "zh",
    title: "在咖啡馆点单",
    title_pt: "Pedindo em um café (chinês)",
    level_min: 1,
    level_max: 4,
    goal: "点一杯饮料并确认大小。",
    setting: "咖啡馆",
  },
  de: {
    id: "cafe-de",
    language: "de",
    title: "Im Café bestellen",
    title_pt: "Pedindo em um café (alemão)",
    level_min: 1,
    level_max: 4,
    goal: "Ein Getränk bestellen und die Größe bestätigen.",
    setting: "Café",
  },
};

/** Same shape as `AppConfigPayload`; only the `skeleton` profile exists. */
export const APP_CONFIG: AppConfigPayload = {
  default_profile: "skeleton",
  profiles: ["skeleton"],
  languages: {
    en: languageEntry("CEFR"),
    ja: languageEntry("JLPT"),
    zh: languageEntry("HSK"),
    de: languageEntry("CEFR"),
  },
};

function languageEntry(levelScale: string): AppConfigPayload["languages"][string] {
  return {
    profile: "skeleton",
    level_scale: levelScale,
    llm: { model: "e2e-llm", endpoint: null },
    stt: { provider: "e2e-stt" },
    tts: { provider: "e2e-tts" },
  };
}

/** Everything the scripted socket replays; assertions read from the same object. */
export const SCRIPT: ScriptedSocketPayload["script"] = {
  opening: "Welcome to the café! What would you like today?",
  replyChunks: ["Nice choice. ", "Would you like oat milk ", "with that?"],
  correction: {
    original: USER_TEXT,
    corrected: "I'd like a coffee, please",
    explanation_pt:
      'Use "I\'d like" em vez de "I want" para soar mais educado, e não esqueça o artigo "a" antes de "coffee".',
    type: "grammar",
    severity: "medium",
    confidence: 0.92,
  },
  vocab: { term: "oat milk", translation_pt: "leite de aveia" },
  goalsMet: ["order a drink"],
  explain: {
    explanation_pt:
      'Frases com "would like" soam como pedido educado; "want" soa direto demais em um café.',
    example_target: "I'd like a coffee, please.",
    example_pt: "Eu gostaria de um café, por favor.",
  },
  aux: {
    en: { translation_pt: "Ótima escolha. Quer leite de aveia junto?" },
    ja: {
      reading: {
        kind: "furigana",
        text: "kyō wa ii tenki desu ne",
        kana: "きょうはいいてんきですね",
        segments: [
          { text: "今日", reading: "きょう", romaji: "kyō" },
          { text: "は", reading: "は", romaji: "wa" },
          { text: "いい", reading: "いい", romaji: "ii" },
          { text: "天気", reading: "てんき", romaji: "tenki" },
          { text: "です", reading: "です", romaji: "desu" },
          { text: "ね", reading: "ね", romaji: "ne" },
        ],
      },
      translation_pt: "Hoje o tempo está bom, né?",
    },
    zh: {
      reading: {
        kind: "pinyin",
        text: "jīn tiān tiān qì hěn hǎo",
        segments: [
          { text: "今天", reading: "jīn tiān", romaji: null },
          { text: "天气", reading: "tiān qì", romaji: null },
          { text: "很好", reading: "hěn hǎo", romaji: null },
        ],
      },
      translation_pt: "Hoje o tempo está muito bom.",
    },
    de: { translation_pt: "Gostaria de um café?" },
  },
};

/**
 * Stubs everything the browser would otherwise ask the backend for, then swaps
 * in the scripted socket. No backend, no models, no external network.
 */
export async function installTutorHarness(page: Page): Promise<void> {
  // Registered first, so the specific routes below take precedence.
  await page.route("**/api/**", (route) =>
    route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: `unmocked API call: ${route.request().url()}` }),
    }),
  );

  await page.route("**/api/config", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(APP_CONFIG),
    }),
  );

  await page.route(/\/api\/scenarios\?language=/, (route) => {
    const requested = new URL(route.request().url()).searchParams.get("language") ?? "en";
    const scenario = SCENARIOS[requested as Language] ?? SCENARIOS.en;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [scenario] }),
    });
  });

  await page.addInitScript(installScriptedSocket, {
    wavB64: SILENT_WAV_B64,
    scenarios: SCENARIOS,
    script: SCRIPT,
  });
}
