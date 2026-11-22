import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OrderedAudioQueue, playInOrder } from "./audioQueue";

/** Stand-in for `HTMLAudioElement`: records plays and lets a test end them. */
class FakeAudio {
  static instances: FakeAudio[] = [];

  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  paused = false;

  constructor(readonly src: string) {
    FakeAudio.instances.push(this);
  }

  play(): Promise<void> {
    return Promise.resolve();
  }

  pause(): void {
    this.paused = true;
  }

  /** Simulates the end of the clip, advancing the queue. */
  finish(): void {
    this.onended?.();
  }
}

function playing(): string[] {
  return FakeAudio.instances.map((audio) => audio.src);
}

beforeEach(() => {
  FakeAudio.instances = [];
  vi.stubGlobal("Audio", FakeAudio);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("OrderedAudioQueue", () => {
  it("plays the sentences in order", () => {
    const queue = new OrderedAudioQueue();
    queue.push(0, "a");
    queue.push(1, "b");
    queue.push(2, "c");

    // Only one clip at a time: the rest waits for the previous `ended`.
    expect(playing()).toEqual(["a"]);

    FakeAudio.instances[0].finish();
    expect(playing()).toEqual(["a", "b"]);

    FakeAudio.instances[1].finish();
    expect(playing()).toEqual(["a", "b", "c"]);
  });

  it("holds back until the missing sequence arrives", () => {
    const queue = new OrderedAudioQueue();
    queue.push(0, "a");
    queue.push(2, "c");

    FakeAudio.instances[0].finish();
    // Seq 1 is still missing, so nothing else may start.
    expect(playing()).toEqual(["a"]);

    queue.push(1, "b");
    expect(playing()).toEqual(["a", "b"]);
    FakeAudio.instances[1].finish();
    expect(playing()).toEqual(["a", "b", "c"]);
  });

  it("advances only once when a clip reports both ended and error", () => {
    const queue = new OrderedAudioQueue();
    queue.push(0, "a");
    queue.push(1, "b");
    queue.push(2, "c");

    const first = FakeAudio.instances[0];
    first.finish();
    // A failed media element may report `error` after `ended`; that must not skip one.
    first.onerror?.();

    expect(playing()).toEqual(["a", "b"]);
    FakeAudio.instances[1].finish();
    expect(playing()).toEqual(["a", "b", "c"]);
  });

  it("drops pending clips and pauses the current one on reset", () => {
    const queue = new OrderedAudioQueue();
    queue.push(0, "a");
    queue.push(1, "b");

    queue.reset();

    expect(FakeAudio.instances[0].paused).toBe(true);
    queue.push(0, "a2");
    expect(playing()).toEqual(["a", "a2"]);
  });
});

describe("playInOrder", () => {
  it("replays every url from the first one", () => {
    playInOrder(["a", "b", "c"]);

    expect(playing()).toEqual(["a"]);
    FakeAudio.instances[0].finish();
    expect(playing()).toEqual(["a", "b"]);
    FakeAudio.instances[1].finish();
    expect(playing()).toEqual(["a", "b", "c"]);
  });

  it("does nothing for an empty reply", () => {
    playInOrder([]);

    expect(playing()).toEqual([]);
  });
});
