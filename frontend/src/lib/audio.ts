export function mergeChunks(chunks: Float32Array[]): Float32Array {
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const merged = new Float32Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.length;
  }
  return merged;
}

export function resampleLinear(
  input: Float32Array,
  inputRate: number,
  outputRate: number,
): Float32Array {
  if (input.length === 0 || inputRate === outputRate) return input;
  if (inputRate <= 0 || outputRate <= 0) throw new Error("sample rates must be positive");

  const duration = input.length / inputRate;
  const outputLength = Math.max(1, Math.round(duration * outputRate));
  const output = new Float32Array(outputLength);
  const step = (input.length - 1) / Math.max(1, outputLength - 1);

  for (let i = 0; i < outputLength; i += 1) {
    const position = i * step;
    const lower = Math.floor(position);
    const upper = Math.min(lower + 1, input.length - 1);
    const weight = position - lower;
    output[i] = input[lower] * (1 - weight) + input[upper] * weight;
  }
  return output;
}

export function toPcm16(samples: Float32Array): ArrayBuffer {
  const buffer = new ArrayBuffer(samples.length * 2);
  const view = new DataView(buffer);
  for (let i = 0; i < samples.length; i += 1) {
    const value = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(i * 2, Math.round(value * 32767), true);
  }
  return buffer;
}

export function wavBase64ToUrl(base64: string): string {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
}

/**
 * Same as `wavBase64ToUrl`, but a payload the server mangled resolves to `null`
 * instead of throwing: `atob` runs during render, so an exception there blanks
 * the whole app.
 */
export function tryWavBase64ToUrl(base64: string | null | undefined): string | null {
  if (!base64) return null;
  try {
    return wavBase64ToUrl(base64);
  } catch {
    return null;
  }
}

/** Releases object URLs created by `wavBase64ToUrl`; unknown urls are ignored. */
export function revokeAudioUrls(urls: Iterable<string>): void {
  for (const url of urls) URL.revokeObjectURL(url);
}

/** Thrown when `cancel()` lands while `start()` is still acquiring devices. */
export class RecordingCancelledError extends Error {
  constructor() {
    super("Gravação cancelada.");
    this.name = "RecordingCancelledError";
  }
}

type RecorderState = "idle" | "starting" | "started";

export class MicrophoneRecorder {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private chunks: Float32Array[] = [];
  private state: RecorderState = "idle";
  /** Set by `cancel()`; the start flow re-checks it after every await. */
  private cancelled = false;

  async start(): Promise<void> {
    if (this.state !== "idle") {
      throw new Error("O microfone já está aberto nesta gravação.");
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("Microfone indisponível neste navegador ou contexto.");
    }
    this.state = "starting";
    this.cancelled = false;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      this.stream = stream;
      this.ensureLive();

      const context = new AudioContext();
      this.context = context;
      await context.audioWorklet.addModule("/pcm-worklet.js");
      this.ensureLive();

      const node = new AudioWorkletNode(context, "pcm-collector");
      this.node = node;
      node.port.onmessage = (event: MessageEvent<Float32Array>) => {
        this.chunks.push(event.data);
      };

      const source = context.createMediaStreamSource(stream);
      const silent = context.createGain();
      silent.gain.value = 0;
      source.connect(node);
      node.connect(silent);
      silent.connect(context.destination);
      await context.resume();
      this.ensureLive();
      this.state = "started";
    } catch (error) {
      this.teardown();
      throw error;
    }
  }

  async stop(): Promise<ArrayBuffer> {
    const sampleRate = this.context?.sampleRate ?? 48_000;
    const chunks = this.chunks;
    this.chunks = [];
    this.teardown();
    return toPcm16(resampleLinear(mergeChunks(chunks), sampleRate, 16_000));
  }

  cancel(): void {
    this.cancelled = true;
    this.chunks = [];
    this.teardown();
  }

  /** Aborts a start that is mid-flight once the awaited device call returns. */
  private ensureLive(): void {
    if (this.cancelled) throw new RecordingCancelledError();
  }

  private teardown(): void {
    this.state = "idle";
    this.node?.disconnect();
    this.node = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    if (this.context) {
      void this.context.close();
      this.context = null;
    }
  }
}
