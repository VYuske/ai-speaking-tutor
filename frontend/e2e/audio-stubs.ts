import type { Page } from "@playwright/test";

/**
 * What the stub installed by `installAudioStubs` recorded. Every field is an
 * in-page counter/array, so a test can assert on object URLs, playback order and
 * microphone life cycle without touching real devices.
 */
export interface AudioStubLog {
  /** Object URLs handed out by `URL.createObjectURL`. */
  created: string[];
  /** URLs passed to `URL.revokeObjectURL`. */
  revoked: string[];
  /** `HTMLMediaElement.play()` calls, in order; the value is the element `src`. */
  plays: string[];
  getUserMediaCalls: number;
  trackStops: number;
  contextsClosed: number;
}

interface AudioStubWindow extends Window {
  __audio?: AudioStubLog;
}

export interface AudioStubOptions {
  /** Delay of the stubbed `getUserMedia`, to reproduce a slow mic acquisition. */
  getUserMediaDelayMs?: number;
}

/** Installs the stubs of `stubAudioEnvironment` before any app code runs. */
export async function installAudioStubs(
  page: Page,
  options: AudioStubOptions = {},
): Promise<void> {
  await page.addInitScript(stubAudioEnvironment, options);
}

/**
 * Replaces the browser audio stack:
 *
 * - `URL.createObjectURL` / `revokeObjectURL` are recorded, so a test can prove
 *   object URLs are revoked (and that every play got a distinct URL);
 * - `HTMLMediaElement.play` resolves at once and fires `ended`, so
 *   `OrderedAudioQueue` walks the whole reply without decoding anything;
 * - `getUserMedia`, `AudioContext` and `AudioWorkletNode` are fakes that count
 *   stream/context teardown, which is how the hot-mic bug is observable.
 *
 * The function is serialized by `page.addInitScript`, so it must not close over
 * module-scope values.
 */
function stubAudioEnvironment(options: AudioStubOptions = {}): void {
  const log: AudioStubLog = {
    created: [],
    revoked: [],
    plays: [],
    getUserMediaCalls: 0,
    trackStops: 0,
    contextsClosed: 0,
  };
  (window as AudioStubWindow).__audio = log;
  const micDelayMs = options.getUserMediaDelayMs ?? 0;

  let urlSeq = 0;
  URL.createObjectURL = () => {
    urlSeq += 1;
    const url = `blob:stub/${urlSeq}`;
    log.created.push(url);
    return url;
  };
  URL.revokeObjectURL = (url: string) => {
    log.revoked.push(url);
  };

  Object.defineProperty(HTMLMediaElement.prototype, "play", {
    configurable: true,
    writable: true,
    value: function play(this: HTMLMediaElement): Promise<void> {
      log.plays.push(this.src);
      window.setTimeout(() => this.dispatchEvent(new Event("ended")), 0);
      return Promise.resolve();
    },
  });
  Object.defineProperty(HTMLMediaElement.prototype, "pause", {
    configurable: true,
    writable: true,
    value: function pause(): void {},
  });

  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: {
      getUserMedia: () => {
        log.getUserMediaCalls += 1;
        return new Promise((resolve) => {
          window.setTimeout(() => {
            resolve({
              getTracks: () => [
                {
                  stop: () => {
                    log.trackStops += 1;
                  },
                },
              ],
            });
          }, micDelayMs);
        });
      },
    },
  });

  class StubAudioWorkletNode {
    port: { onmessage: ((event: MessageEvent<Float32Array>) => void) | null } = {
      onmessage: null,
    };

    connect(): void {}

    disconnect(): void {}
  }

  class StubAudioContext {
    sampleRate = 48_000;
    state = "running";
    destination = {};
    audioWorklet = { addModule: () => Promise.resolve() };

    createMediaStreamSource() {
      return { connect: () => {} };
    }

    createGain() {
      return { gain: { value: 0 }, connect: () => {} };
    }

    resume() {
      return Promise.resolve();
    }

    close() {
      log.contextsClosed += 1;
      return Promise.resolve();
    }
  }

  Object.defineProperty(window, "AudioContext", {
    configurable: true,
    writable: true,
    value: StubAudioContext,
  });
  Object.defineProperty(window, "AudioWorkletNode", {
    configurable: true,
    writable: true,
    value: StubAudioWorkletNode,
  });
}

/** Snapshot of everything the audio stubs recorded so far. */
export function getAudioLog(page: Page): Promise<AudioStubLog> {
  return page.evaluate(() => {
    const log = (window as AudioStubWindow).__audio;
    if (!log) throw new Error("installAudioStubs was not installed for this page");
    return log;
  });
}
