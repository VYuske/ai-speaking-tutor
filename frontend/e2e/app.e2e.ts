import { expect, test, type Locator, type Page } from "@playwright/test";
import { SCRIPT, SCENARIOS, USER_TEXT, installTutorHarness } from "./harness";
import { WARMUP_FAILED_MESSAGE, getSentFrames } from "./scripted-socket";

const REPLY = SCRIPT.replyChunks.join("");
const JA_AUX = SCRIPT.aux.ja;
const ZH_AUX = SCRIPT.aux.zh;
const EN_TRANSLATION = SCRIPT.aux.en.translation_pt ?? "";
const JA_KANA = JA_AUX.reading?.kana ?? "";
const JA_ROMAJI = JA_AUX.reading?.text ?? "";
const ZH_PINYIN = ZH_AUX.reading?.text ?? "";

test.beforeEach(async ({ page }) => {
  await installTutorHarness(page);
});

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

/** One bordered card per correction, anchored on the correction type it renders. */
function correctionCards(main: Locator): Locator {
  return main.locator("div.rounded-xl").filter({ hasText: SCRIPT.correction.type });
}

async function chooseLanguage(page: Page, label: RegExp): Promise<void> {
  await page.getByRole("button", { name: label }).click();
}

/** Opens the reading/translation popover of the newest tutor bubble. */
async function openLastPopover(page: Page): Promise<Locator> {
  await page.getByRole("button", { name: "Leitura e tradução" }).last().click();
  const popover = page.getByRole("dialog", { name: "Leitura e tradução" });
  await expect(popover).toBeVisible();
  return popover;
}

test("setup screen renders and Começar sessão enters the chat", async ({ page }) => {
  await openSetup(page);

  await expect(page.getByRole("button", { name: /Inglês/ })).toBeVisible();
  await expect(page.getByText(SCENARIOS.en.title_pt, { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Começar sessão" })).toBeEnabled();

  await page.getByRole("button", { name: "Começar sessão" }).click();

  await expect(page.getByRole("button", { name: "Encerrar" })).toBeVisible();
  await expect(page.locator("header")).toContainText(SCENARIOS.en.title_pt);
  await expect(page.locator("main")).toContainText(SCRIPT.opening);

  const frames = await getSentFrames(page);
  expect(frames).toHaveLength(1);
  expect(frames[0]).toMatchObject({
    type: "session.start",
    language: "en",
    level: 3,
    scenario_id: SCENARIOS.en.id,
    profile: "skeleton",
  });
});

test("text input shows the user bubble, the streamed reply and the correction card", async ({
  page,
}) => {
  await startSession(page);
  await sendText(page, USER_TEXT);

  const main = page.locator("main");
  await expect(main.getByText(USER_TEXT, { exact: true })).toBeVisible();
  await expect(main.getByText(REPLY, { exact: true })).toBeVisible();

  const cards = correctionCards(main);
  await expect(cards).toHaveCount(1);
  await expect(cards.getByText(SCRIPT.correction.type, { exact: true })).toBeVisible();
  await expect(
    main.getByText(`${SCRIPT.vocab.term} · ${SCRIPT.vocab.translation_pt}`, { exact: true }),
  ).toHaveCount(1);
  await expect(page.locator("footer")).toContainText("Pronto");

  const frames = await getSentFrames(page);
  expect(frames.at(-1)).toMatchObject({ type: "text.input", text: USER_TEXT });
});

test("correction card shows the corrected text and the PT-BR explanation", async ({ page }) => {
  await startSession(page);
  await sendText(page, USER_TEXT);

  const main = page.locator("main");
  const cards = correctionCards(main);
  await expect(cards).toHaveCount(1);
  await expect(cards.locator("p.line-through")).toHaveText(SCRIPT.correction.original);
  await expect(cards.getByText(SCRIPT.correction.corrected, { exact: true })).toBeVisible();
  await expect(cards.getByText(SCRIPT.correction.explanation_pt, { exact: true })).toBeVisible();
});

test("Encerrar opens the review modal with the turn text", async ({ page }) => {
  await startSession(page);
  await sendText(page, USER_TEXT);
  await expect(page.locator("main")).toContainText(REPLY);

  await page.getByRole("button", { name: "Encerrar" }).click();

  // Modals have no test id (`src/` is out of scope), so anchor on their overlay classes.
  const modal = page.locator("div.fixed.inset-0.z-50");
  await expect(modal.getByRole("heading", { name: "Sessão concluída" })).toBeVisible();
  await expect(modal).toContainText(USER_TEXT);
  await expect(modal).toContainText(REPLY);
  await expect(modal).toContainText(SCRIPT.correction.explanation_pt);
  await expect(modal.getByText(`${SCRIPT.correction.type}: 1`)).toBeVisible();

  await modal.getByRole("button", { name: "Fechar" }).click();
  await expect(page.getByRole("heading", { name: "AI Speaking Tutor" })).toBeVisible();
});

test("Explicar em PT is answered with explain.result", async ({ page }) => {
  await startSession(page);
  await sendText(page, USER_TEXT);

  const explain = page.getByRole("button", { name: "Explicar em PT" });
  await expect(explain).toBeVisible();
  await explain.click();

  const modal = page.locator("div.fixed.inset-0.z-50");
  await expect(modal.getByRole("heading", { name: "Em português" })).toBeVisible();
  await expect(modal).toContainText(SCRIPT.explain.explanation_pt);
  await expect(modal).toContainText(SCRIPT.explain.example_target);

  const frames = await getSentFrames(page);
  expect(frames.at(-1)).toMatchObject({ type: "explain.request", turn_id: 1 });
});

test("warm-up pill shows while the model loads and clears on warmup.done", async ({ page }) => {
  await openSetup(page);
  // Hold `warmup.done` back so the pending state is observable.
  await page.evaluate(() => {
    (window as Window & { __warmupDelayMs?: number }).__warmupDelayMs = 800;
  });

  await page.getByRole("button", { name: "Começar sessão" }).click();
  await expect(page.getByRole("button", { name: "Encerrar" })).toBeVisible();

  const pill = page.locator("footer").getByText(/carregando modelo|modelo pronto/);
  await expect(pill).toHaveText("carregando modelo…");

  // Warm-up is advisory: the opening turn and push-to-talk stay available.
  await expect(page.locator("main")).toContainText(SCRIPT.opening);
  await expect(page.locator("footer button")).toBeEnabled();

  await expect(pill).toHaveText("modelo pronto");
  await expect(pill).toHaveCount(0, { timeout: 5_000 });
});

test("warmup.failed shows an unavailable pill and a warning toast", async ({ page }) => {
  await openSetup(page);
  await page.evaluate(() => {
    (window as Window & { __warmupFailed?: boolean }).__warmupFailed = true;
  });

  await page.getByRole("button", { name: "Começar sessão" }).click();
  await expect(page.getByRole("button", { name: "Encerrar" })).toBeVisible();

  await expect(page.locator("footer").getByText("modelo indisponível")).toBeVisible();
  await expect(page.getByText(WARMUP_FAILED_MESSAGE)).toBeVisible();
  await expect(page.locator("footer button")).toBeEnabled();
});

test("free mode starts without an opening and answers the first message", async ({ page }) => {
  await openSetup(page);

  await page.getByRole("button", { name: "Conversa livre" }).click();
  // No scenario list in free mode; the session starts straight away.
  await expect(page.getByText(SCENARIOS.en.title_pt, { exact: true })).toHaveCount(0);

  await page.getByRole("button", { name: "Começar sessão" }).click();
  await expect(page.getByRole("button", { name: "Encerrar" })).toBeVisible();

  await expect(page.locator("header")).toContainText("Conversa livre");
  await expect(page.locator("main")).toContainText("Comece falando sobre o que quiser");
  await expect(page.locator("main")).not.toContainText(SCRIPT.opening);

  await sendText(page, USER_TEXT);
  await expect(page.locator("main").getByText(USER_TEXT, { exact: true })).toBeVisible();
  await expect(page.locator("main").getByText(REPLY, { exact: true })).toBeVisible();
  await expect(page.locator("main")).not.toContainText("Comece falando sobre o que quiser");

  const frames = await getSentFrames(page);
  expect(frames[0]).toMatchObject({
    type: "session.start",
    mode: "free",
    aids: { translation: "auto", reading: true },
  });
  expect(frames[0]).not.toHaveProperty("scenario_id");
});

test("reading popover shows kana, romaji and the PT translation", async ({ page }) => {
  await openSetup(page);
  await chooseLanguage(page, /Japonês/);
  await page.getByRole("button", { name: "Começar sessão" }).click();

  await sendText(page, "こんにちは");
  // Wait for the streamed reply so `.last()` targets its bubble, not the opening.
  await expect(page.locator("main")).toContainText(REPLY);
  const popover = await openLastPopover(page);

  await expect(popover).toContainText("Kana");
  await expect(popover).toContainText(JA_KANA);
  await expect(popover).toContainText("Romaji");
  await expect(popover).toContainText(JA_ROMAJI);
  await expect(popover).toContainText(JA_AUX.translation_pt ?? "");

  await page.keyboard.press("Escape");
  await expect(popover).toHaveCount(0);
});

test("reading popover shows pinyin for chinese and no kana line", async ({ page }) => {
  await openSetup(page);
  await chooseLanguage(page, /Chinês/);
  await page.getByRole("button", { name: "Começar sessão" }).click();

  await sendText(page, "你好");
  await expect(page.locator("main")).toContainText(REPLY);
  const popover = await openLastPopover(page);

  await expect(popover).toContainText("Pinyin");
  await expect(popover).toContainText(ZH_PINYIN);
  await expect(popover).not.toContainText("Kana");
  await expect(popover).toContainText(ZH_AUX.translation_pt ?? "");
});

test("popover shows carregando leitura… until the reading arrives", async ({ page }) => {
  await openSetup(page);
  await chooseLanguage(page, /Japonês/);
  // Hold the aid frames back so the loading state is observable.
  await page.evaluate(() => {
    (window as Window & { __auxDelayMs?: number }).__auxDelayMs = 3000;
  });
  await page.getByRole("button", { name: "Começar sessão" }).click();

  await sendText(page, "こんにちは");
  await expect(page.locator("main")).toContainText(REPLY);

  const popover = await openLastPopover(page);
  await expect(popover).toContainText("carregando leitura…");
  // The open popover updates itself when the late aux lands.
  await expect(popover).toContainText(JA_KANA, { timeout: 10_000 });
  await expect(popover).toContainText(JA_ROMAJI, { timeout: 10_000 });
});

test("manual translation offers Traduzir and shows the result on demand", async ({ page }) => {
  await openSetup(page);
  await page.getByRole("button", { name: "Desligada (botão)" }).click();
  await page.getByRole("button", { name: "Começar sessão" }).click();

  await sendText(page, USER_TEXT);
  await expect(page.locator("main")).toContainText(REPLY);

  const frames = await getSentFrames(page);
  expect(frames[0]).toMatchObject({
    type: "session.start",
    aids: { translation: "manual" },
  });

  await page.evaluate(() => {
    (window as Window & { __translateDelayMs?: number }).__translateDelayMs = 600;
  });

  const popover = await openLastPopover(page);
  await expect(popover).not.toContainText(EN_TRANSLATION);

  await popover.getByRole("button", { name: "Traduzir" }).click();

  await expect(popover).toContainText("traduzindo…");
  await expect(popover).toContainText(EN_TRANSLATION);
  await expect(popover.getByRole("button", { name: "Traduzir" })).toHaveCount(0);

  expect((await getSentFrames(page)).at(-1)).toMatchObject({
    type: "translate.request",
    turn_id: 1,
  });
});

test("header quick toggle flips PT auto/PT botão and sends aids.update", async ({ page }) => {
  await startSession(page);

  const header = page.locator("header");
  await expect(header.getByRole("button", { name: "PT auto" })).toBeVisible();
  await header.getByRole("button", { name: "PT auto" }).click();

  await expect(header.getByRole("button", { name: "PT botão" })).toBeVisible();
  expect((await getSentFrames(page)).at(-1)).toMatchObject({
    type: "aids.update",
    translation: "manual",
  });
});

test("mode and translation preferences survive a reload", async ({ page }) => {
  await openSetup(page);
  await page.getByRole("button", { name: "Conversa livre" }).click();
  await page.getByRole("button", { name: "Desligada (botão)" }).click();

  await page.reload();

  await expect(page.getByRole("button", { name: "Conversa livre" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.getByRole("button", { name: "Desligada (botão)" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  // Free mode stays selected, so the scenario grid is still hidden.
  await expect(page.getByText(SCENARIOS.en.title_pt, { exact: true })).toHaveCount(0);
});
