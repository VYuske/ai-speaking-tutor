import { expect, test, type Page } from "@playwright/test";
import { getAudioLog, installAudioStubs } from "./audio-stubs";
import { SCRIPT, USER_TEXT, installTutorHarness } from "./harness";
import { getSentFrames } from "./scripted-socket";

/**
 * Hardening of the chat surface (AST-054): push-to-talk life cycle, replay of
 * every reply sentence, closed-socket input, keyboard ownership, explain
 * matching, aids adoption and translation failures. The backend is fully
 * scripted; the browser audio stack is stubbed by `installAudioStubs`.
 */

const REPLY = SCRIPT.replyChunks.join("");
const EN_TRANSLATION = SCRIPT.aux.en.translation_pt ?? "";
const POPOVER = "Leitura e tradução";
const OFFLINE_TEXT = "Sem conexão: a mensagem não foi enviada.";
const MISMATCH_TOAST = "Explicação de outro turno ignorada.";

test.beforeEach(async ({ page }) => {
  await installTutorHarness(page);
});

/** Writes flags the scripted socket reads (`__replyInSentences`, `__errorOnTurn`, ...). */
async function setFlags(page: Page, flags: Record<string, unknown>): Promise<void> {
  await page.evaluate((values) => {
    Object.assign(window as unknown as Record<string, unknown>, values);
  }, flags);
}

async function openSetup(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "AI Speaking Tutor" })).toBeVisible();
}

async function startSession(page: Page): Promise<void> {
  await openSetup(page);
  const start = page.getByRole("button", { name: "Começar sessão" });
  await expect(start).toBeEnabled();
  await start.click();
  await expect(page.getByRole("button", { name: "Encerrar" })).toBeVisible();
}

async function sendText(page: Page, text: string): Promise<void> {
  const input = page.getByPlaceholder(/Ou digite aqui/);
  await input.fill(text);
  await input.press("Enter");
}

async function frameTypes(page: Page): Promise<string[]> {
  return (await getSentFrames(page)).map((frame) => String(frame.type));
}

async function countFrames(page: Page, type: string): Promise<number> {
  return (await getSentFrames(page)).filter((frame) => frame.type === type).length;
}

/** Holds the push-to-talk button with the mouse (a real pointer, so capture works). */
async function pressPtt(page: Page): Promise<void> {
  const box = await page.getByRole("button", { name: "Segure para falar" }).boundingBox();
  if (!box) throw new Error("push-to-talk button has no box");
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
}

test("a quick tap on push-to-talk sends audio.start and audio.end and releases the mic", async ({
  page,
}) => {
  // A slow `getUserMedia` is what makes a tap land before `recorder.start()` resolves.
  await installAudioStubs(page, { getUserMediaDelayMs: 150 });
  await startSession(page);

  await pressPtt(page);
  await page.mouse.up();

  await expect.poll(() => countFrames(page, "audio.end")).toBe(1);
  expect(await frameTypes(page)).toEqual(["session.start", "audio.start", "audio.end"]);

  const first = await getAudioLog(page);
  expect(first.getUserMediaCalls).toBe(1);
  expect(first.trackStops).toBe(1);
  expect(first.contextsClosed).toBe(1);

  // The recorder is reusable: a second hold opens and closes its own stream.
  await pressPtt(page);
  await expect(page.getByRole("button", { name: "Solte para enviar" })).toBeVisible();
  await page.mouse.up();
  await expect.poll(() => countFrames(page, "audio.end")).toBe(2);

  const second = await getAudioLog(page);
  expect(second.getUserMediaCalls).toBe(2);
  expect(second.trackStops).toBe(2);
  await expect(page.getByRole("button", { name: "Segure para falar" })).toBeEnabled();
});

test("the push-to-talk button starts and stops the mic from the keyboard", async ({ page }) => {
  await installAudioStubs(page);
  await startSession(page);

  await page.getByRole("button", { name: "Segure para falar" }).focus();
  await page.keyboard.down("Enter");
  await expect(page.getByRole("button", { name: "Solte para enviar" })).toBeVisible();
  await page.keyboard.up("Enter");

  await expect.poll(() => countFrames(page, "audio.end")).toBe(1);
  expect(await frameTypes(page)).toEqual(["session.start", "audio.start", "audio.end"]);
  expect((await getAudioLog(page)).trackStops).toBe(1);
});

test("Space does not open the mic while a control has focus", async ({ page }) => {
  await installAudioStubs(page);
  await startSession(page);

  const toggle = page.locator("header").getByRole("button", { name: "PT auto" });
  await toggle.focus();
  await page.keyboard.press("Space");

  // The focused button keeps its own Space: it toggles the translation mode.
  await expect(page.locator("header").getByRole("button", { name: "PT botão" })).toBeVisible();

  const input = page.getByPlaceholder(/Ou digite aqui/);
  await input.focus();
  await page.keyboard.type("oi ");
  await expect(input).toHaveValue("oi ");

  expect(await frameTypes(page)).not.toContain("audio.start");
  expect((await getAudioLog(page)).getUserMediaCalls).toBe(0);
});

test("losing the window while holding push-to-talk closes the microphone", async ({ page }) => {
  await installAudioStubs(page);
  await startSession(page);

  await pressPtt(page);
  await expect(page.getByRole("button", { name: "Solte para enviar" })).toBeVisible();

  await page.evaluate(() => window.dispatchEvent(new Event("blur")));

  await expect.poll(() => countFrames(page, "audio.end")).toBe(1);
  await expect(page.getByRole("button", { name: "Segure para falar" })).toBeVisible();
  expect((await getAudioLog(page)).trackStops).toBe(1);
});

test("Ouvir replays every sentence of the reply and the session revokes its urls", async ({
  page,
}) => {
  await installAudioStubs(page);
  await startSession(page);
  await setFlags(page, { __replyInSentences: true });

  await sendText(page, USER_TEXT);
  await expect(page.locator("main")).toContainText(REPLY);

  // Opening (1 frame) plus the three sentences of the reply, played in order.
  await expect.poll(async () => (await getAudioLog(page)).plays.length).toBe(4);
  const played = (await getAudioLog(page)).plays;
  const replyUrls = played.slice(1);
  expect(replyUrls).toHaveLength(3);
  expect(new Set(played).size).toBe(4);

  await page.getByRole("button", { name: "Ouvir" }).last().click();
  await expect.poll(async () => (await getAudioLog(page)).plays.length).toBe(7);
  expect((await getAudioLog(page)).plays.slice(4)).toEqual(replyUrls);

  // Back to the setup screen: every object URL handed out must be revoked.
  await page.getByRole("button", { name: "Encerrar" }).click();
  const modal = page.locator("div.fixed.inset-0.z-50");
  await modal.getByRole("button", { name: "Fechar" }).click();
  await expect(page.getByRole("heading", { name: "AI Speaking Tutor" })).toBeVisible();

  const log = await getAudioLog(page);
  expect(log.created.length).toBeGreaterThanOrEqual(4);
  expect(log.revoked).toEqual(expect.arrayContaining(log.created));
});

test("an invalid explain wav keeps the app alive and offers no player", async ({ page }) => {
  await installAudioStubs(page);
  await startSession(page);
  await sendText(page, USER_TEXT);
  await expect(page.locator("main")).toContainText(REPLY);
  await setFlags(page, { __explainInvalidWav: true });

  await page.getByRole("button", { name: "Explicar em PT" }).first().click();

  const modal = page.locator("div.fixed.inset-0.z-50");
  await expect(modal.getByRole("heading", { name: "Em português" })).toBeVisible();
  await expect(modal).toContainText(SCRIPT.explain.explanation_pt);
  await expect(modal.getByRole("button", { name: "Ouvir exemplo" })).toHaveCount(0);
  // Not blanked: the chat is still there behind the modal.
  await expect(page.getByRole("button", { name: "Encerrar" })).toBeVisible();

  await modal.getByRole("button", { name: "Fechar" }).click();
  await expect(modal).toHaveCount(0);
});

test("a render crash shows Voltar ao início and recovers the app", async ({ page }) => {
  await installAudioStubs(page);
  await openSetup(page);
  // `session.ready` without a model map makes the header throw while rendering.
  await setFlags(page, { __brokenReady: true });

  await page.getByRole("button", { name: "Começar sessão" }).click();

  const fallback = page.getByRole("alert");
  await expect(fallback).toContainText("Algo deu errado");
  await fallback.getByRole("button", { name: "Voltar ao início" }).click();

  await expect(page.getByRole("heading", { name: "AI Speaking Tutor" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Começar sessão" })).toBeEnabled();
});

test("Explicar em PT ignores an explain.result for another turn", async ({ page }) => {
  await installAudioStubs(page);
  await startSession(page);
  await sendText(page, USER_TEXT);
  await expect(page.locator("main")).toContainText(REPLY);
  await sendText(page, "second turn");
  await expect(page.locator("main").getByText("second turn", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Explicar em PT" })).toHaveCount(2);

  const modal = page.locator("div.fixed.inset-0.z-50");
  await setFlags(page, { __explainTurnOffset: 1 });
  await page.getByRole("button", { name: "Explicar em PT" }).first().click();

  await expect(page.getByText(MISMATCH_TOAST)).toBeVisible();
  await expect(modal).toHaveCount(0);
  expect((await getSentFrames(page)).at(-1)).toMatchObject({
    type: "explain.request",
    turn_id: 1,
  });

  await setFlags(page, { __explainTurnOffset: 0 });
  await page.getByRole("button", { name: "Explicar em PT" }).first().click();

  await expect(modal.getByRole("heading", { name: "Em português" })).toBeVisible();
  await expect(modal).toContainText("Turno 1");
});

test("the explain modal revokes its audio url when it closes", async ({ page }) => {
  await installAudioStubs(page);
  await startSession(page);
  await sendText(page, USER_TEXT);
  await expect(page.locator("main")).toContainText(REPLY);
  await setFlags(page, { __explainAudio: true });

  await page.getByRole("button", { name: "Explicar em PT" }).first().click();
  const modal = page.locator("div.fixed.inset-0.z-50");
  await expect(modal.getByRole("button", { name: "Ouvir exemplo" })).toBeVisible();

  const before = await getAudioLog(page);
  const modalUrl = before.created.at(-1);
  expect(modalUrl).toBeDefined();

  await modal.getByRole("button", { name: "Fechar" }).click();
  await expect(modal).toHaveCount(0);
  await expect.poll(async () => (await getAudioLog(page)).revoked).toContain(modalUrl);
});

test("the header adopts the translation mode from session.ready.aids", async ({ page }) => {
  await installAudioStubs(page);
  await openSetup(page);
  // The server disagrees with the request: it answers `manual`.
  await setFlags(page, { __forceAidsTranslation: "manual" });

  await page.getByRole("button", { name: "Começar sessão" }).click();

  const header = page.locator("header");
  await expect(header.getByRole("button", { name: "PT botão" })).toBeVisible();
  await expect(header.getByRole("button", { name: "PT auto" })).toHaveCount(0);

  const frames = await getSentFrames(page);
  expect(frames.map((frame) => frame.type)).toEqual(["session.start"]);
  expect(frames[0]).toMatchObject({ aids: { translation: "auto" } });

  // The adopted mode is mirrored locally, so a reload comes back as Desligada.
  await page.reload();
  await expect(page.getByRole("button", { name: "Desligada (botão)" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
});

test("a failed automatic translation offers the Traduzir button", async ({ page }) => {
  await installAudioStubs(page);
  await startSession(page);
  await setFlags(page, { __translationFailed: true });

  await sendText(page, USER_TEXT);
  await expect(page.locator("main")).toContainText(REPLY);

  await page.getByRole("button", { name: POPOVER }).last().click();
  const popover = page.getByRole("dialog", { name: POPOVER });
  await expect(popover.getByRole("button", { name: "Traduzir" })).toBeVisible();
  await expect(popover).not.toContainText("carregando tradução…");

  await popover.getByRole("button", { name: "Traduzir" }).click();
  await expect(popover).toContainText(EN_TRANSLATION);
  expect((await getSentFrames(page)).at(-1)).toMatchObject({
    type: "translate.request",
    turn_id: 1,
  });
});

test("a mid-session close keeps the typed text and warns instead of hanging", async ({ page }) => {
  await installAudioStubs(page);
  await startSession(page);
  await setFlags(page, { __closeAfterTurn: true });

  await sendText(page, USER_TEXT);
  await expect(page.locator("main")).toContainText(REPLY);
  await expect(page.getByRole("button", { name: "Voltar ao início" })).toBeVisible();

  const input = page.getByPlaceholder(/Ou digite aqui/);
  await input.fill("ainda aqui?");
  await input.press("Enter");

  await expect(page.getByText(OFFLINE_TEXT)).toBeVisible();
  await expect(input).toHaveValue("ainda aqui?");
  expect(await countFrames(page, "text.input")).toBe(1);
});

test("an error frame resets the busy state and shows the message", async ({ page }) => {
  await installAudioStubs(page);
  await startSession(page);
  await setFlags(page, { __errorOnTurn: "o modelo falhou (e2e)" });

  await sendText(page, USER_TEXT);

  await expect(page.getByText("o modelo falhou (e2e)")).toBeVisible();
  await expect(page.locator("footer button")).toBeEnabled();
});
