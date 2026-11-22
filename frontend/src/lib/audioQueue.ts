export class OrderedAudioQueue {
  private pending = new Map<number, string>();
  private nextSeq = 0;
  private current: HTMLAudioElement | null = null;

  push(seq: number, url: string): void {
    this.pending.set(seq, url);
    this.drain();
  }

  reset(): void {
    this.pending.clear();
    this.nextSeq = 0;
    if (this.current) {
      this.current.pause();
      this.current = null;
    }
  }

  private drain(): void {
    if (this.current) return;
    const url = this.pending.get(this.nextSeq);
    if (!url) return;
    this.pending.delete(this.nextSeq);

    const audio = new Audio(url);
    this.current = audio;
    const advance = () => {
      // A failed element can report `ended` and `error` for the same clip: advancing
      // twice would silently skip the next sentence.
      if (this.current !== audio) return;
      this.current = null;
      this.nextSeq += 1;
      this.drain();
    };
    audio.onended = advance;
    audio.onerror = advance;
    void audio.play().catch(advance);
  }
}

/**
 * Plays a whole reply from its first sentence. Every call builds a fresh queue,
 * so pressing "Ouvir" again restarts the reply instead of resuming it.
 */
export function playInOrder(urls: readonly string[]): OrderedAudioQueue {
  const queue = new OrderedAudioQueue();
  urls.forEach((url, seq) => queue.push(seq, url));
  return queue;
}
