/** Two decoded buffers share one clock. Switching only changes gains, never sources. */
export class ABPlayer {
  private context: AudioContext | null = null;
  private buffers: [AudioBuffer, AudioBuffer] | null = null;
  private nodes: AudioBufferSourceNode[] = [];
  private gains: GainNode[] = [];
  private master: GainNode | null = null;
  private offset = 0;
  private startedAt = 0;
  private generation = 0;
  playing = false;
  side: 0 | 1 = 0;
  volume = 0.7;
  get duration(): number {
    return this.buffers?.[0].duration ?? 0;
  }
  get position(): number {
    return Math.min(
      this.duration,
      this.offset +
        (this.playing && this.context
          ? Math.max(0, this.context.currentTime - this.startedAt)
          : 0),
    );
  }
  async load(original: Blob, effect: Blob): Promise<void> {
    this.pause();
    this.offset = 0;
    this.context ??= new AudioContext();
    const context = this.context;
    const [a, b] = await Promise.all([
      original.arrayBuffer().then((x) => context.decodeAudioData(x)),
      effect.arrayBuffer().then((x) => context.decodeAudioData(x)),
    ]);
    if (
      a.length !== b.length ||
      a.sampleRate !== b.sampleRate ||
      a.numberOfChannels !== b.numberOfChannels
    ) {
      throw new Error("A/B 音频长度或采样率不一致，请重新生成试听。");
    }
    this.buffers = [a, b];
  }
  async play(): Promise<void> {
    if (!this.context || !this.buffers || this.playing) return;
    await this.context.resume();
    if (this.offset >= this.duration) this.offset = 0;
    const generation = ++this.generation;
    this.master = this.context.createGain();
    this.master.gain.value = this.volume;
    this.master.connect(this.context.destination);
    this.startedAt = this.context.currentTime + 0.025;
    this.playing = true;
    this.gains = this.buffers.map((buffer, index) => {
      const source = this.context!.createBufferSource();
      const gain = this.context!.createGain();
      source.buffer = buffer;
      gain.gain.value = index === this.side ? 1 : 0;
      source.connect(gain);
      gain.connect(this.master!);
      this.nodes.push(source);
      if (index === 0)
        source.onended = () => {
          if (generation === this.generation) {
            this.pause();
            this.offset = this.duration;
          }
        };
      source.start(this.startedAt, this.offset);
      return gain;
    });
  }
  pause(): void {
    this.offset = this.position;
    this.playing = false;
    this.generation++;
    this.nodes.forEach((node) => {
      node.onended = null;
      node.stop();
      node.disconnect();
    });
    this.nodes = [];
    this.gains.forEach((gain) => gain.disconnect());
    this.gains = [];
    this.master?.disconnect();
    this.master = null;
  }
  seek(seconds: number): void {
    const wasPlaying = this.playing;
    this.pause();
    this.offset = Math.min(this.duration, Math.max(0, seconds));
    if (wasPlaying) void this.play();
  }
  switchTo(side: 0 | 1): void {
    this.side = side;
    if (!this.context) return;
    const now = this.context.currentTime;
    this.gains.forEach((gain, index) => {
      gain.gain.cancelAndHoldAtTime(now);
      gain.gain.linearRampToValueAtTime(index === side ? 1 : 0, now + 0.008);
    });
  }
  setVolume(volume: number): void {
    this.volume = volume;
    if (this.context && this.master)
      this.master.gain.setTargetAtTime(volume, this.context.currentTime, 0.01);
  }
  dispose(): void {
    this.pause();
    void this.context?.close();
    this.context = null;
    this.buffers = null;
  }
}
