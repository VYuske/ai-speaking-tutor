import { describe, expect, it } from "vitest";
import {
  expectsReading,
  mergeAux,
  readingBadge,
  readingView,
  translationView,
} from "./aids";
import type { AssistantAux, ChatMessage, Reading } from "../types";

const FURIGANA: Reading = {
  kind: "furigana",
  text: "kyou wa ii tenki desu ne",
  kana: "きょうはいいてんきですね",
  segments: [
    { text: "今日", reading: "きょう", romaji: "kyou" },
    { text: "は", reading: "は", romaji: "wa" },
  ],
};

const PINYIN: Reading = {
  kind: "pinyin",
  text: "nǐ hǎo",
  segments: [
    { text: "你", reading: "nǐ", romaji: null },
    { text: "好", reading: "hǎo", romaji: null },
  ],
};

function assistant(turnId: number, text: string, aux?: AssistantAux): ChatMessage {
  return { id: `assistant-${turnId}`, turnId, role: "assistant", text, aux };
}

function user(turnId: number, text: string): ChatMessage {
  return { id: `user-${turnId}`, turnId, role: "user", text };
}

describe("mergeAux", () => {
  it("attaches the reading to the matching assistant turn only", () => {
    const messages = [assistant(0, "こんにちは"), user(1, "hello"), assistant(1, "はい")];

    const merged = mergeAux(messages, 1, { reading: FURIGANA });

    expect(merged[2].aux?.reading).toEqual(FURIGANA);
    expect(merged[0].aux).toBeUndefined();
    expect(merged[1].aux).toBeUndefined();
  });

  it("merges a late translation into a turn that already has a reading", () => {
    const withReading = mergeAux([assistant(1, "はい")], 1, { reading: FURIGANA });

    const merged = mergeAux(withReading, 1, { translation_pt: "Sim." });

    expect(merged[0].aux).toEqual({ reading: FURIGANA, translation_pt: "Sim." });
  });

  it("keeps the reading when only the translation is patched twice", () => {
    const withReading = mergeAux([assistant(1, "はい")], 1, { reading: FURIGANA });
    const withTranslation = mergeAux(withReading, 1, { translation_pt: "Sim." });

    const again = mergeAux(withTranslation, 1, { translation_pt: "Sim, senhor." });

    expect(again[0].aux?.reading).toEqual(FURIGANA);
    expect(again[0].aux?.translation_pt).toBe("Sim, senhor.");
  });

  it("ignores a turn that is not in the thread", () => {
    const messages = [assistant(0, "こんにちは")];

    const merged = mergeAux(messages, 7, { translation_pt: "Oi" });

    expect(merged).toBe(messages);
    expect(merged).toHaveLength(1);
    expect(merged[0].aux).toBeUndefined();
  });

  it("does not mutate the previous message or array", () => {
    const previous = assistant(1, "はい");
    const messages = [previous];

    const merged = mergeAux(messages, 1, { reading: FURIGANA, translation_pt: "Sim." });

    expect(previous.aux).toBeUndefined();
    expect(messages).toHaveLength(1);
    expect(merged).not.toBe(messages);
    expect(merged[0]).not.toBe(previous);
    expect(merged[0].text).toBe("はい");
  });
});

describe("translationView", () => {
  it("offers the button when the mode is manual and nothing was requested", () => {
    expect(translationView(undefined, "manual", false)).toEqual({ kind: "button" });
  });

  it("shows the pending state while an on-demand translation is in flight", () => {
    expect(translationView({ reading: FURIGANA }, "manual", true)).toEqual({ kind: "pending" });
  });

  it("prefers the translation once it arrives, whichever mode asked for it", () => {
    expect(translationView({ translation_pt: "Sim." }, "manual", true)).toEqual({
      kind: "ready",
      text: "Sim.",
    });
    expect(translationView({ translation_pt: "Sim." }, "auto", false)).toEqual({
      kind: "ready",
      text: "Sim.",
    });
  });

  it("waits for the automatic translation instead of offering the button", () => {
    expect(translationView({ reading: FURIGANA }, "auto", false)).toEqual({ kind: "waiting" });
  });
});

describe("translationView when the translation never arrives", () => {
  it("offers the button when the automatic translation failed", () => {
    expect(translationView({ translation_failed: true }, "auto", false)).toEqual({
      kind: "button",
    });
  });

  it("offers the button once the turn ended without a translation", () => {
    expect(translationView({ reading: FURIGANA }, "auto", false, true)).toEqual({ kind: "button" });
  });

  it("keeps waiting while the turn is still running", () => {
    expect(translationView({ reading: FURIGANA }, "auto", false, false)).toEqual({
      kind: "waiting",
    });
  });

  it("prefers the translation that arrived late over the failure flag", () => {
    expect(
      translationView({ translation_pt: "Sim.", translation_failed: true }, "auto", false),
    ).toEqual({ kind: "ready", text: "Sim." });
  });

  it("leaves the pending state of a manual request when it fails", () => {
    expect(translationView({ translation_failed: true }, "manual", true)).toEqual({
      kind: "button",
    });
  });
});

describe("mergeAux translation failures", () => {
  it("records a failed automatic translation on the turn", () => {
    const merged = mergeAux([assistant(1, "はい")], 1, { translation_failed: true });

    expect(merged[0].aux?.translation_failed).toBe(true);
  });

  it("keeps the reading and clears the flag when the translation lands later", () => {
    const failed = mergeAux([assistant(1, "はい")], 1, {
      reading: FURIGANA,
      translation_failed: true,
    });

    const translated = mergeAux(failed, 1, { translation_pt: "Sim." });

    expect(translated[0].aux).toEqual({ reading: FURIGANA, translation_pt: "Sim." });
  });

  it("ignores a frame that carries nothing", () => {
    const messages = [assistant(1, "はい")];

    expect(mergeAux(messages, 1, {})).toBe(messages);
  });
});

describe("readingView", () => {
  it("returns kana and romaji for japanese", () => {
    expect(readingView({ reading: FURIGANA }, "ja")).toEqual({
      kana: "きょうはいいてんきですね",
      phoneticLabel: "Romaji",
      phonetic: "kyou wa ii tenki desu ne",
    });
  });

  it("returns pinyin without a kana line for chinese", () => {
    expect(readingView({ reading: PINYIN }, "zh")).toEqual({
      phoneticLabel: "Pinyin",
      phonetic: "nǐ hǎo",
    });
  });

  it("returns nothing for languages without reading or without aux", () => {
    expect(readingView({ reading: FURIGANA }, "en")).toEqual({});
    expect(readingView({}, "ja")).toEqual({});
    expect(readingView(undefined, "zh")).toEqual({});
  });
});

describe("readingBadge", () => {
  it("labels the trigger per language", () => {
    expect(readingBadge("ja").label).toBe("あ");
    expect(readingBadge("zh").label).toBe("拼");
    expect(readingBadge("en").label).toBe("PT");
    expect(readingBadge("de").label).toBe("PT");
  });

  it("gives every trigger an accessible name", () => {
    for (const language of ["ja", "zh", "en", "de"] as const) {
      expect(readingBadge(language).ariaLabel).toMatch(/leitura e tradução/i);
    }
  });
});

describe("expectsReading", () => {
  it("is true only for japanese and chinese", () => {
    expect(expectsReading("ja")).toBe(true);
    expect(expectsReading("zh")).toBe(true);
    expect(expectsReading("en")).toBe(false);
    expect(expectsReading("de")).toBe(false);
  });
});
