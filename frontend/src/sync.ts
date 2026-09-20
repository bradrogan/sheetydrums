// Drives the on-score playhead + note highlight from a playback time, and turns
// clicks on the score into seek times. Pure geometry over the RenderModel — no
// knowledge of the player itself.
import { BAR_SVG_WIDTH, BAR_SVG_HEIGHT, type RenderModel, type BarView } from './render';

/** highlight band width, in viewBox (800-wide) units. */
const HIGHLIGHT_WIDTH = 18;

/** viewBox-x → percentage of the (possibly scaled) bar width. */
function pct(x: number): number {
  return (x / BAR_SVG_WIDTH) * 100;
}

export class SyncController {
  private bars: BarView[];
  private activeIndex = -1;
  /** Last time passed to update(); -1 until playback has positioned once. */
  private lastTime = -1;
  /** When true, clicks edit the score instead of seeking playback. */
  editMode = false;
  /** Set by the caller; invoked when a click on the score maps to a seek time. */
  onSeek: (seconds: number) => void = () => {};
  /** Set by the caller; invoked (in edit mode) with the clicked bar + local svg coords. */
  onEditClick: (bar: BarView, x: number, y: number) => void = () => {};
  /** Edit-mode lane-drag hooks (Phase 2d): a drag paints a lane × bar selection.
   * The lane is derived by the caller from the start-bar's local svg `y`. */
  onLaneDragStart: (bar: BarView, y: number) => void = () => {};
  onLaneDragTo: (bar: BarView) => void = () => {};
  onLaneDragEnd: () => void = () => {};

  /** BarView by bar index, for locating the bar under the cursor mid-drag. */
  private byIndex = new Map<number, BarView>();
  /** True while a lane-drag is in progress. */
  private dragging = false;

  constructor(model: RenderModel) {
    // Sorted by start time (bars come in order, but be defensive).
    this.bars = [...model.bars].sort((a, b) => a.startSeconds - b.startSeconds);
    for (const bar of this.bars) {
      this.byIndex.set(bar.index, bar);
      bar.svgHost.style.cursor = 'pointer';
      bar.svgHost.addEventListener('click', (e) => this.handleClick(bar, e));
      bar.svgHost.addEventListener('mousedown', (e) => this.handlePointerDown(bar, e));
    }
  }

  /** Start of a potential lane-drag (edit mode only). A drag only begins once the
   * pointer moves past a small threshold, so a plain tap still opens the column
   * editor via the click handler. */
  private handlePointerDown(bar: BarView, e: MouseEvent): void {
    if (!this.editMode || e.button !== 0) return;
    const rect = bar.svgHost.getBoundingClientRect();
    const startY = ((e.clientY - rect.top) / rect.height) * BAR_SVG_HEIGHT;
    const startClientX = e.clientX;
    const startClientY = e.clientY;
    this.dragging = false;

    const onMove = (me: MouseEvent): void => {
      if (!this.dragging) {
        if (Math.hypot(me.clientX - startClientX, me.clientY - startClientY) < 4) return;
        this.dragging = true;
        preventSelection(true);
        window.getSelection()?.removeAllRanges();
        this.onLaneDragStart(bar, startY);
      }
      const overBar = this.barUnder(me.clientX, me.clientY);
      if (overBar) this.onLaneDragTo(overBar);
      me.preventDefault();
    };
    const onUp = (): void => {
      document.removeEventListener('mousemove', onMove, true);
      document.removeEventListener('mouseup', onUp, true);
      preventSelection(false);
      if (this.dragging) {
        this.dragging = false;
        this.onLaneDragEnd();
        // Swallow the click this drag will synthesize, wherever it lands: a
        // cross-bar drag's click targets the shared container (not a bar's
        // svgHost), so a per-bar flag would never clear. Capture it once at the
        // document, then self-remove; the fallback timeout covers the case where
        // no click fires at all (drag released off-window).
        const swallow = (ce: MouseEvent): void => {
          ce.stopPropagation();
          ce.preventDefault();
          cleanup();
        };
        const cleanup = (): void => document.removeEventListener('click', swallow, true);
        document.addEventListener('click', swallow, true);
        setTimeout(cleanup, 0);
      }
    };
    document.addEventListener('mousemove', onMove, true);
    document.addEventListener('mouseup', onUp, true);
  }

  private barUnder(clientX: number, clientY: number): BarView | undefined {
    const row = (document.elementFromPoint(clientX, clientY) as Element | null)?.closest('.bar-row');
    const idx = row instanceof HTMLElement ? Number(row.dataset.barIndex) : NaN;
    return Number.isNaN(idx) ? undefined : this.byIndex.get(idx);
  }

  /** Position the playhead + highlight for playback time `t` (seconds). */
  update(t: number): void {
    this.lastTime = t;
    const idx = this.findBar(t);
    if (idx === -1) {
      this.clear();
      return;
    }
    const bar = this.bars[idx]!;

    if (idx !== this.activeIndex) {
      // Hide the previously-active bar's overlays and reveal this one's.
      if (this.activeIndex !== -1) hideOverlays(this.bars[this.activeIndex]!);
      this.activeIndex = idx;
      // Pin the active bar near the top so the upcoming bars are visible ahead.
      bar.row.scrollIntoView({ block: 'start', behavior: 'smooth' });
    }

    const f = clamp((t - bar.startSeconds) / (bar.endSeconds - bar.startSeconds), 0, 1);
    const x = playheadX(bar, f);

    // Positions are expressed as a % of the viewBox width so overlays stay
    // aligned when the SVG scales to fit its column (1 or 2 bars per row).
    bar.playhead.style.left = `${pct(x)}%`;
    bar.playhead.classList.add('active');

    // Highlight the most recent note column at or before the playhead.
    const note = latestNoteBefore(bar, x);
    if (note) {
      bar.highlight.style.left = `${pct(note.xPx - HIGHLIGHT_WIDTH / 2)}%`;
      bar.highlight.style.width = `${pct(HIGHLIGHT_WIDTH)}%`;
      bar.highlight.classList.add('active');
    } else {
      bar.highlight.classList.remove('active');
    }
  }

  /**
   * Re-apply the last playhead position onto the (possibly redrawn) bars.
   * Call after the score is re-rendered — e.g. entering/leaving edit mode swaps
   * every bar between proportional and grid layout, which moves the note x's the
   * playhead is anchored to. Because the active bar is unchanged, this repositions
   * without re-scrolling. No-op before playback has positioned the playhead once.
   */
  refresh(): void {
    if (this.lastTime >= 0) this.update(this.lastTime);
  }

  /** Hide all overlays (e.g. when playback is reset or out of range). */
  clear(): void {
    if (this.activeIndex !== -1) {
      hideOverlays(this.bars[this.activeIndex]!);
      this.activeIndex = -1;
    }
  }

  private findBar(t: number): number {
    // Binary search for the last bar whose startSeconds <= t.
    const bars = this.bars;
    if (bars.length === 0 || t < bars[0]!.startSeconds) return -1;
    let lo = 0;
    let hi = bars.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (bars[mid]!.startSeconds <= t) lo = mid;
      else hi = mid - 1;
    }
    // Past the last bar's end → no active bar.
    if (t > bars[lo]!.endSeconds) return -1;
    return lo;
  }

  private handleClick(bar: BarView, e: MouseEvent): void {
    // Convert the click from rendered pixels back into viewBox (800×140) units,
    // accounting for however much the SVG is scaled to fit its column.
    const rect = bar.svgHost.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * BAR_SVG_WIDTH;
    const y = ((e.clientY - rect.top) / rect.height) * BAR_SVG_HEIGHT;
    if (this.editMode) {
      this.onEditClick(bar, x, y);
      return;
    }
    const f = clamp((x - bar.contentX0) / (bar.contentX1 - bar.contentX0), 0, 1);
    const t = bar.startSeconds + f * (bar.endSeconds - bar.startSeconds);
    this.onSeek(t);
  }
}

/**
 * Map a bar-fraction f (0..1 of the bar's duration) to an x, anchored to the
 * actual note x-positions so the playhead lands ON each notehead at its moment.
 * VexFlow spaces notes musically (non-linear), so a straight linear-in-time
 * mapping visibly drifts from the noteheads; interpolating between the captured
 * note positions keeps them in sync. Endpoints anchor to the bar's edges.
 */
function playheadX(bar: BarView, f: number): number {
  const lerp = (x0: number, y0: number, x1: number, y1: number, x: number): number =>
    x1 === x0 ? y0 : y0 + ((y1 - y0) * (x - x0)) / (x1 - x0);

  let prevF = 0;
  let prevX = bar.contentX0;
  for (const note of bar.notes) {
    const nf = bar.barWholeNotes > 0 ? note.position / bar.barWholeNotes : 0;
    if (f <= nf) return lerp(prevF, prevX, nf, note.xPx, f);
    prevF = nf;
    prevX = note.xPx;
  }
  return lerp(prevF, prevX, 1, bar.contentX1, f);
}

function latestNoteBefore(bar: BarView, x: number): BarView['notes'][number] | null {
  let best: BarView['notes'][number] | null = null;
  for (const note of bar.notes) {
    if (note.xPx <= x + HIGHLIGHT_WIDTH / 2) {
      if (!best || note.xPx > best.xPx) best = note;
    }
  }
  return best;
}

function hideOverlays(bar: BarView): void {
  bar.playhead.classList.remove('active');
  bar.highlight.classList.remove('active');
}

// Suppress native text/element selection for the duration of a lane-drag. Edit
// mode re-enables user-select (see style.css), so without this a drag would
// paint a browser text selection over the score.
let _selectstartBlocker: ((e: Event) => void) | null = null;
function preventSelection(on: boolean): void {
  if (on && !_selectstartBlocker) {
    _selectstartBlocker = (e: Event): void => e.preventDefault();
    document.addEventListener('selectstart', _selectstartBlocker, true);
  } else if (!on && _selectstartBlocker) {
    document.removeEventListener('selectstart', _selectstartBlocker, true);
    _selectstartBlocker = null;
  }
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}
