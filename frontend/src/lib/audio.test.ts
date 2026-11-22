import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  MicrophoneRecorder,
  RecordingCancelledError,
  mergeChunks,
  resampleLinear,
  toPcm16,
  tryWavBase64ToUrl,
} from "./audio";

describe("mergeChunks", () => {
  it("concatenates in order", () => {
    const merged = mergeChunks([
      new Float32Array([1, 2]),
      new Float32Array([3]),
      new Float32Array([4, 5]),
    ]);
    expect(Array.from(merged)).toEqual([1, 2, 3, 4, 5]);
  });

  it("handles empty input", () => {
    expect(mergeChunks([]).length).toBe(0);
  });
});

describe("resampleLinear", () => {
  it("keeps duration when downsampling", () => {
    const input = new Float32Array(48_000).fill(0.5);
    const output = resampleLinear(input, 48_000, 16_000);
    expect(output.length).toBeCloseTo(16_000, -1);
    expect(output[0]).toBeCloseTo(0.5, 5);
  });

  it("returns the same array when rates match", () => {
    const input = new Float32Array([1, 2, 3]);
    expect(resampleLinear(input, 16_000, 16_000)).toBe(input);
  });

  it("throws on invalid rates", () => {
    expect(() => resampleLinear(new Float32Array([1]), 0, 16_000)).toThrow();
  });
});

describe("tryWavBase64ToUrl", () => {
  let createObjectURL: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    createObjectURL = vi.fn(() => "blob:test");
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      writable: true,
      value: createObjectURL,
    });
  });

  afterEach(() => {
    Reflect.deleteProperty(URL, "createObjectURL");
  });

  it("returns null for a payload that is not base64", () => {
    expect(tryWavBase64ToUrl("%%%isto-nao-e-base64%%%")).toBeNull();
    expect(createObjectURL).not.toHaveBeenCalled();
  });

  it("returns null when there is no payload at all", () => {
    expect(tryWavBase64ToUrl(null)).toBeNull();
    expect(tryWavBase64ToUrl("")).toBeNull();
    expect(createObjectURL).not.toHaveBeenCalled();
  });

  it("builds a blob url for a valid payload", () => {
    expect(tryWavBase64ToUrl(btoa("RIFF....WAVE"))).toBe("blob:test");
    expect(createObjectURL).toHaveBeenCalledTimes(1);
  });
});

interface BrowserStub {
  log: { trackStops: number; contextsClosed: number; getUserMediaCalls: number };
  stream: () => unknown;
  addModuleCalls: () => number;
}

/** Minimal stand-ins for the browser audio surface `MicrophoneRecorder` touches. */
function stubBrowser(
  overrides: {
    getUserMedia?: () => Promise<unknown>;
    addModule?: () => Promise<void>;
  } = {},
): BrowserStub {
  const log = { trackStops: 0, contextsClosed: 0, getUserMediaCalls: 0 };
  let addModuleCalls = 0;
  const stream = () => ({
    getTracks: () => [
      {
        stop: () => {
          log.trackStops += 1;
        },
      },
    ],
  });

  class FakeAudioContext {
    sampleRate = 48_000;
    destination = {};
    audioWorklet = {
      addModule: () => {
        addModuleCalls += 1;
        return overrides.addModule?.() ?? Promise.resolve();
      },
    };

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

  class FakeAudioWorkletNode {
    port: { onmessage: ((event: MessageEvent<Float32Array>) => void) | null } = {
      onmessage: null,
    };

    connect() {}

    disconnect() {}
  }

  vi.stubGlobal("AudioContext", FakeAudioContext);
  vi.stubGlobal("AudioWorkletNode", FakeAudioWorkletNode);
  vi.stubGlobal("navigator", {
    mediaDevices: {
      getUserMedia: () => {
        log.getUserMediaCalls += 1;
        return overrides.getUserMedia?.() ?? Promise.resolve(stream());
      },
    },
  });

  return { log, stream, addModuleCalls: () => addModuleCalls };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("MicrophoneRecorder", () => {
  it("tears the stream and the context down when start fails", async () => {
    const stub = stubBrowser({
      addModule: () => Promise.reject(new Error("worklet indisponível")),
    });
    const recorder = new MicrophoneRecorder();

    await expect(recorder.start()).rejects.toThrow("worklet indisponível");

    expect(stub.log.getUserMediaCalls).toBe(1);
    expect(stub.log.trackStops).toBe(1);
    expect(stub.log.contextsClosed).toBe(1);
  });

  it("is usable again after a failed start", async () => {
    stubBrowser({ addModule: () => Promise.reject(new Error("worklet indisponível")) });
    await expect(new MicrophoneRecorder().start()).rejects.toThrow();

    const retry = stubBrowser();
    const recorder = new MicrophoneRecorder();
    await recorder.start();
    await recorder.stop();

    expect(retry.log.getUserMediaCalls).toBe(1);
    expect(retry.log.trackStops).toBe(1);
    expect(retry.log.contextsClosed).toBe(1);
  });

  it("stops the stream that arrives after a cancel during start", async () => {
    let deliver: ((stream: unknown) => void) | null = null;
    const stub = stubBrowser({
      getUserMedia: () =>
        new Promise((resolve) => {
          deliver = resolve;
        }),
    });
    const recorder = new MicrophoneRecorder();

    const started = recorder.start();
    const rejected = expect(started).rejects.toBeInstanceOf(RecordingCancelledError);
    recorder.cancel();
    deliver!(stub.stream());

    await rejected;
    expect(stub.log.trackStops).toBe(1);
    // The context is only created after the stream, so the cancel never opened one.
    expect(stub.log.contextsClosed).toBe(0);
  });

  it("closes the stream and the context when cancel lands after the device opened", async () => {
    let openWorklet: (() => void) | null = null;
    const stub = stubBrowser({
      addModule: () =>
        new Promise<void>((resolve) => {
          openWorklet = resolve;
        }),
    });
    const recorder = new MicrophoneRecorder();

    const started = recorder.start();
    const rejected = expect(started).rejects.toBeInstanceOf(RecordingCancelledError);
    await vi.waitFor(() => expect(openWorklet).not.toBeNull());
    recorder.cancel();
    openWorklet!();

    await rejected;
    expect(stub.log.trackStops).toBe(1);
    expect(stub.log.contextsClosed).toBe(1);
  });

  it("refuses a second start instead of opening a second stream", async () => {
    const stub = stubBrowser();
    const recorder = new MicrophoneRecorder();
    await recorder.start();

    await expect(recorder.start()).rejects.toThrow(/já está aberto/i);

    expect(stub.log.getUserMediaCalls).toBe(1);
    recorder.cancel();
    expect(stub.log.trackStops).toBe(1);
  });
});

describe("toPcm16", () => {
  it("writes little-endian int16 samples", () => {
    const buffer = toPcm16(new Float32Array([0, 0.5, -0.5, 1]));
    const view = new Int16Array(buffer);
    expect(view.length).toBe(4);
    expect(view[0]).toBe(0);
    expect(view[1]).toBe(16384);
    expect(view[2]).toBe(-16383);
    expect(view[3]).toBe(32767);
  });

  it("clips out-of-range values", () => {
    const view = new Int16Array(toPcm16(new Float32Array([2, -2])));
    expect(view[0]).toBe(32767);
    expect(view[1]).toBe(-32767);
  });
});
