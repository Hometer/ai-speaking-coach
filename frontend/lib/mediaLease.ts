/** A pending browser permission belongs to the action that requested it. */
export class MediaLease {
  private epoch = 0;
  private pending = false;
  begin(): number | null {
    if (this.pending) return null;
    this.pending = true;
    return ++this.epoch;
  }
  accept(epoch: number, stream: MediaStream): boolean {
    if (epoch !== this.epoch) { stream.getTracks().forEach(track => track.stop()); return false; }
    this.pending = false;
    return true;
  }
  fail(epoch: number): boolean { if (epoch !== this.epoch) return false; this.pending = false; return true; }
  cancel(): void { this.epoch++; this.pending = false; }
}
