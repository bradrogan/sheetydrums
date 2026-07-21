// VexFlow's `GhostNote` is an invisible tickable that occupies time without
// drawing anything — we use it to hold silent rest time. (Note: this is NOT a
// drum "ghost note", which is a soft snare hit; that's a separate v2 feature.)
// Aliased to `InvisibleRest` so our code never conflates the two.
import {
  Renderer, Stave, StaveNote, Voice, Formatter, Stem, Beam, Tuplet, Dot,
  GhostNote as InvisibleRest,
} from 'vexflow';
import type { DrumTranscriptionEventsV1Draft, Note } from './generated/events';

type SchemaDrumClass = Note['instrument'];

/**
 * Mapping from each schema drum class to its position on the 5-line percussion
 * staff, and whether it uses an X notehead (for cymbals + hi-hat).
 *
 * Position uses VexFlow's pitch-coordinate convention even though we render
 * with the percussion clef. Treble-clef mnemonic for the staff lines (bottom
 * to top): E G B D F (E/4, G/4, B/4, D/5, F/5). Spaces: F A C E.
 *
 *   - Above top line: G/5 (hi-hat / closed/open)
 *   - Above staff:    A/5 (crash)
 *   - Top line F/5:   ride
 *   - 4th space E/5:  high tom
 *   - 4th line D/5:   mid tom
 *   - 3rd space C/5:  snare
 *   - 2nd space A/4:  low (floor) tom
 *   - 1st space F/4:  kick
 *   - Below staff D/4: hi-hat chick (pedal)
 */
const DRUM_POSITION: Record<SchemaDrumClass, { key: string; xNotehead: boolean }> = {
  kick:         { key: 'f/4', xNotehead: false },
  snare:        { key: 'c/5', xNotehead: false },
  hihat_closed: { key: 'g/5', xNotehead: true },
  hihat_open:   { key: 'g/5', xNotehead: true },
  hihat_chick:  { key: 'd/4', xNotehead: true },
  ride:         { key: 'f/5', xNotehead: true },
  crash:        { key: 'a/5', xNotehead: true },
  tom_high:     { key: 'e/5', xNotehead: false },
  tom_mid:      { key: 'd/5', xNotehead: false },
  tom_low:      { key: 'a/4', xNotehead: false },
};

/**
 * Schema `duration` strings (`"1"`, `"1/4"`, ...) → VexFlow duration codes.
 * Used only for *tuplet* notes — for them the schema's duration is the
 * *written* note value (e.g. "1/8" for an 8th-note triplet member), with the
 * actual played duration scaled by normal/actual inside the Tuplet wrapper.
 * Non-tuplet notes use gap-based duration instead.
 */
const VF_DURATION: Record<Note['duration'], string> = {
  '1':    'w',
  '1/2':  'h',
  '1/4':  'q',
  '1/8':  '8',
  '1/16': '16',
  '1/32': '32',
};

/**
 * Map gap-to-next-hit (in 1/16ths of a whole note) → VexFlow duration code.
 * Drum notation convention: the *written* duration of a non-tuplet hit is
 * determined by when the NEXT hit anywhere on the kit lands, not by the
 * schema's per-note duration field. So we recompute from position gaps for
 * non-tuplet notes.
 */
function gapToDuration(gapInSixteenths: number): string {
  // Clean lookups first (exact 16ths).
  switch (gapInSixteenths) {
    case 16: return 'w';   // whole
    case 12: return 'hd';  // dotted half
    case 8:  return 'h';   // half
    case 6:  return 'qd';  // dotted quarter
    case 4:  return 'q';   // quarter
    case 3:  return '8d';  // dotted eighth
    case 2:  return '8';   // eighth
    case 1:  return '16';  // sixteenth
  }
  // Irregular gap (e.g. 5, 7, 9, ...): round down to nearest representable
  // duration. The result loses 1-3 sixteenths of accuracy at most; a proper
  // fix is tied-note rendering, which is v2 work.
  if (gapInSixteenths >= 12) return 'hd';
  if (gapInSixteenths >= 8)  return 'h';
  if (gapInSixteenths >= 6)  return 'qd';
  if (gapInSixteenths >= 4)  return 'q';
  if (gapInSixteenths >= 3)  return '8d';
  if (gapInSixteenths >= 2)  return '8';
  return '16';
}

const BAR_SVG_WIDTH = 800;
const BAR_SVG_HEIGHT = 140;
const STAVE_X = 10;
const STAVE_Y = 30;
const STAVE_WIDTH = 760;

/** One rendered note's geometry, keyed to its position in the bar. */
export interface NoteView {
  /** Position within the bar as a whole-note fraction (0 = downbeat). */
  position: number;
  /** x of the note column in the 800-coordinate SVG space (== on-screen px). */
  xPx: number;
  instrument: SchemaDrumClass;
}

/** Everything the sync + edit layers need about one rendered bar. */
export interface BarView {
  index: number;
  startSeconds: number;
  endSeconds: number;
  /** The `.bar-row` element (label + svg host). Stable across redraws. */
  row: HTMLElement;
  /** The `.bar-svg` host — positioning context for overlays; stable across redraws. */
  svgHost: HTMLDivElement;
  playhead: HTMLElement;
  highlight: HTMLElement;
  /** Left/right x of the note-laying region (getNoteStartX/getNoteEndX). */
  contentX0: number;
  contentX1: number;
  svgHeight: number;
  notes: NoteView[];
  /** In grid (edit) mode: x of every 16th slot 0..maxSixteenth, for snapping. */
  gridXs?: number[];
  /** Whether this bar drew the clef + time signature (the first bar). */
  showClef: boolean;
  timeSig: string;
}

export interface RenderModel {
  bars: BarView[];
  /** y-pixel (800-coord space) of each instrument's staff line — for hit-testing. */
  instrumentYs: Record<SchemaDrumClass, number>;
}

/** The 10 schema classes, in top-to-bottom staff order (for edit dropdowns). */
export const SCHEMA_CLASSES: SchemaDrumClass[] = [
  'crash', 'hihat_open', 'hihat_closed', 'ride', 'tom_high',
  'tom_mid', 'snare', 'tom_low', 'kick', 'hihat_chick',
];

export type { SchemaDrumClass };

/**
 * Staff-line number for each instrument (VexFlow convention: line 0 = top line
 * F/5, increasing downward, half-steps for spaces). Derived from DRUM_POSITION;
 * used to place added notes at the clicked pitch.
 */
const INSTRUMENT_LINE: Record<SchemaDrumClass, number> = {
  crash: -1, hihat_open: -0.5, hihat_closed: -0.5, ride: 0, tom_high: 0.5,
  tom_mid: 1, snare: 1.5, tom_low: 2.5, kick: 3.5, hihat_chick: 4.5,
};

function computeInstrumentYs(): Record<SchemaDrumClass, number> {
  const probe = new Stave(STAVE_X, STAVE_Y, STAVE_WIDTH);
  const ys = {} as Record<SchemaDrumClass, number>;
  for (const cls of SCHEMA_CLASSES) {
    ys[cls] = probe.getYForLine(INSTRUMENT_LINE[cls]);
  }
  return ys;
}

export function renderScore(
  container: HTMLElement,
  events: DrumTranscriptionEventsV1Draft,
): RenderModel {
  container.innerHTML = '';

  const tsLabel = `${events.time_signature.numerator}/${events.time_signature.denominator}`;
  const bars = events.bars;

  // Bar length in seconds from tempo × time signature — used to give the last
  // bar an end time (all earlier bars end at the next bar's start).
  const [num, den] = tsLabel.split('/').map(Number);
  const beatsPerBar = num ?? 4;
  const secPerBeat = 60 / events.tempo_bpm;
  // A "beat" in start_seconds terms is a quarter note; scale by 4/den for the
  // meter's beat unit.
  const barSeconds = secPerBeat * beatsPerBar * (4 / (den ?? 4));

  const barViews: BarView[] = [];
  for (const [i, bar] of bars.entries()) {
    const startSeconds = bar.start_seconds;
    const endSeconds =
      i + 1 < bars.length ? bars[i + 1]!.start_seconds : startSeconds + barSeconds;
    const bv = makeBarView(bar.index, startSeconds, endSeconds, i === 0, tsLabel);
    container.appendChild(bv.row);
    drawBar(bv, bar);
    barViews.push(bv);
  }
  return { bars: barViews, instrumentYs: computeInstrumentYs() };
}

/** Build the (empty) DOM skeleton for one bar. Drawing happens in drawBar. */
function makeBarView(
  index: number,
  startSeconds: number,
  endSeconds: number,
  showClef: boolean,
  timeSig: string,
): BarView {
  const row = document.createElement('div');
  row.className = 'bar-row';
  row.dataset.barIndex = String(index);

  const label = document.createElement('div');
  label.className = 'bar-label';
  label.textContent = `Bar ${index}`;
  row.appendChild(label);

  const svgHost = document.createElement('div');
  svgHost.className = 'bar-svg';
  row.appendChild(svgHost);

  return {
    index,
    startSeconds,
    endSeconds,
    row,
    svgHost,
    playhead: document.createElement('div'),
    highlight: document.createElement('div'),
    contentX0: STAVE_X,
    contentX1: STAVE_X + STAVE_WIDTH,
    svgHeight: BAR_SVG_HEIGHT,
    notes: [],
    showClef,
    timeSig,
  };
}

/**
 * Draw (or redraw) `bar` into an existing BarView's svgHost. Clears the host
 * first, so it's safe to call repeatedly after edits — the svgHost element
 * itself is preserved so click handlers bound to it survive.
 */
export function drawBar(
  barView: BarView,
  bar: DrumTranscriptionEventsV1Draft['bars'][number],
  gridMode = false,
): void {
  const { svgHost, timeSig, showClef } = barView;
  svgHost.innerHTML = '';
  barView.notes = [];
  barView.gridXs = undefined;

  // Playhead + highlight overlays live inside `.bar-svg` (position:relative) so
  // they scroll with the 800px-wide SVG content on narrow screens.
  const highlight = document.createElement('div');
  highlight.className = 'note-highlight';
  svgHost.appendChild(highlight);
  const playhead = document.createElement('div');
  playhead.className = 'playhead';
  svgHost.appendChild(playhead);
  barView.highlight = highlight;
  barView.playhead = playhead;

  const renderer = new Renderer(svgHost, Renderer.Backends.SVG);
  renderer.resize(BAR_SVG_WIDTH, BAR_SVG_HEIGHT);
  const ctx = renderer.getContext();
  // VexFlow writes width/height attributes on its SVG but no viewBox.
  // Add one so print CSS can scale the SVG to the page width.
  const svgEl = svgHost.querySelector('svg');
  if (svgEl) {
    svgEl.setAttribute('viewBox', `0 0 ${BAR_SVG_WIDTH} ${BAR_SVG_HEIGHT}`);
    svgEl.setAttribute('preserveAspectRatio', 'xMidYMid meet');
  }

  const stave = new Stave(STAVE_X, STAVE_Y, STAVE_WIDTH);
  if (showClef) {
    stave.addClef('percussion').addTimeSignature(timeSig);
  }
  stave.setContext(ctx).draw();

  // The x-range VexFlow lays notes into — the playhead maps bar-time linearly
  // across this region.
  barView.contentX0 = stave.getNoteStartX();
  barView.contentX1 = stave.getNoteEndX();

  const [num, den] = timeSig.split('/').map(Number);
  // Bar length expressed in 1/16ths of a whole note.
  // (numerator / denominator) gives the bar in whole notes; × 16 gives 16ths.
  const barLengthInSixteenths = Math.round(((num ?? 4) / (den ?? 4)) * 16);
  // One beat = 1/den of a whole note → this many 1/16ths. Beams are grouped by
  // beat, so this drives beam grouping.
  const sixteenthsPerBeat = Math.max(1, Math.round(16 / (den ?? 4)));

  // Grid (edit) mode: fixed 16th-note lattice so notes never shift as you edit.
  if (gridMode) {
    drawGridBar(ctx, stave, barView, bar, num ?? 4, den ?? 4, barLengthInSixteenths, sixteenthsPerBeat);
    return;
  }

  // Readable view rendering: tile the bar with notes + rests. Tuplet bars fall
  // back to the (rest-free) gap-based path below, since tuplets don't fit the
  // straight 16th tiling. (No tuplets appear in v1 output today.)
  if (!bar.notes.some((n) => n.tuplet)) {
    const { tickables, noteColumns, beamGroups } = buildBarVoice(
      bar.notes,
      barLengthInSixteenths,
      sixteenthsPerBeat,
    );
    const voice = new Voice({ numBeats: num ?? 4, beatValue: den ?? 4 });
    voice.setStrict(false);
    voice.addTickables(tickables);
    const beams = beamGroups.map((group) => new Beam(group));
    new Formatter().joinVoices([voice]).format([voice], STAVE_WIDTH - 80);
    voice.draw(ctx, stave);
    for (const beam of beams) beam.setContext(ctx).draw();
    for (const col of noteColumns) {
      barView.notes.push({
        position: col.position,
        xPx: col.note.getAbsoluteX(),
        instrument: col.instrument,
      });
    }
    return;
  }

  const { staveNotes, tuplets, positions, leadingRests, beamGroups } = buildStaveNotes(
    bar.notes,
    barLengthInSixteenths,
    sixteenthsPerBeat,
  );
  if (staveNotes.length === 0) return;

  // strict=false lets the rendered voice be flexible about exact totals
  // (real transcribed bars rarely sum to a perfect whole-note worth of duration).
  const voice = new Voice({ numBeats: num ?? 4, beatValue: den ?? 4 });
  voice.setStrict(false);
  // The leading rests occupy the silent time before the first hit so the
  // formatter places notes at their true metric position (see buildStaveNotes).
  voice.addTickables([...leadingRests, ...staveNotes]);

  // Beam per beat (see buildStaveNotes). Build the Beams BEFORE drawing the
  // voice: Beam construction marks each note as "in a beam", and StaveNote
  // checks that flag during draw() to skip its own stem-flag. Drawing the voice
  // first would render individual flags with the beam overlaid on top.
  const beams = beamGroups.map((group) => new Beam(group));

  new Formatter().joinVoices([voice]).format([voice], STAVE_WIDTH - 80);
  voice.draw(ctx, stave);

  for (const beam of beams) {
    beam.setContext(ctx).draw();
  }
  // Tuplet brackets/labels drawn last so they sit above the beams.
  for (const tuplet of tuplets) {
    tuplet.setContext(ctx).draw();
  }

  // Capture each note-column's laid-out x (post-format) for playhead/highlight
  // alignment and Phase-2 hit-testing. One entry per StaveNote (a chord shares
  // a column); instrument is the first hit's, used only for display tinting.
  for (let i = 0; i < staveNotes.length; i++) {
    barView.notes.push({
      position: positions[i]!.value,
      xPx: staveNotes[i]!.getAbsoluteX(),
      instrument: positions[i]!.instrument,
    });
  }
}

/**
 * Grid (edit) rendering. Keeps the *same* note values + beaming as view mode
 * (via buildStaveNotes) but forces each note's x onto a fixed per-16th-slot
 * formula (setXShift after formatting) so positions are deterministic — a note
 * never moves when you add or delete others. Gridlines are drawn behind, one per
 * 16th, with quarter-beats emphasized. Because a slot is exactly 1/16 wide, a
 * note's written value still matches the space it spans (a dotted eighth covers
 * three slots, etc.).
 */
function drawGridBar(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ctx: any,
  stave: Stave,
  barView: BarView,
  bar: DrumTranscriptionEventsV1Draft['bars'][number],
  num: number,
  den: number,
  maxSixteenth: number,
  sixteenthsPerBeat: number,
): void {
  const span = barView.contentX1 - barView.contentX0;
  const gridX = (slot: number): number => barView.contentX0 + (slot / maxSixteenth) * span;

  // Gridlines first so notes render on top. One line per 16th, plus the closing
  // barline; quarter-beats (every sixteenthsPerBeat) are emphasized.
  const gridXs: number[] = [];
  for (let i = 0; i <= maxSixteenth; i++) gridXs.push(gridX(i));
  drawGridlines(ctx, stave, gridXs, sixteenthsPerBeat);
  barView.gridXs = gridXs;

  const { staveNotes, tuplets, positions, leadingRests, beamGroups } = buildStaveNotes(
    bar.notes,
    maxSixteenth,
    sixteenthsPerBeat,
  );
  if (staveNotes.length === 0) return;

  const voice = new Voice({ numBeats: num, beatValue: den });
  voice.setStrict(false);
  voice.addTickables([...leadingRests, ...staveNotes]);
  const beams = beamGroups.map((group) => new Beam(group));
  new Formatter().joinVoices([voice]).format([voice], STAVE_WIDTH - 80);

  // Override the formatter's proportional x with the fixed grid slot x.
  for (let i = 0; i < staveNotes.length; i++) {
    const slot = Math.round(positions[i]!.value * 16);
    staveNotes[i]!.setXShift(gridX(slot) - staveNotes[i]!.getAbsoluteX());
  }

  voice.draw(ctx, stave);
  for (const beam of beams) beam.setContext(ctx).draw();
  for (const tuplet of tuplets) tuplet.setContext(ctx).draw();

  for (let i = 0; i < staveNotes.length; i++) {
    barView.notes.push({
      position: positions[i]!.value,
      xPx: staveNotes[i]!.getNoteHeadBeginX(),
      instrument: positions[i]!.instrument,
    });
  }
}

function drawGridlines(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ctx: any,
  stave: Stave,
  gridXs: number[],
  sixteenthsPerBeat: number,
): void {
  const yTop = stave.getYForLine(-2);
  const yBottom = stave.getYForLine(5.5);
  for (let i = 0; i < gridXs.length; i++) {
    const isBeat = i % sixteenthsPerBeat === 0;
    ctx.save();
    ctx.setStrokeStyle(isBeat ? '#9aa4b2' : '#e2e6ea');
    ctx.setLineWidth(isBeat ? 1.4 : 1);
    ctx.beginPath();
    ctx.moveTo(gridXs[i]!, yTop);
    ctx.lineTo(gridXs[i]!, yBottom);
    ctx.stroke();
    ctx.restore();
  }
}

type PositionInfo = { value: number; instrument: SchemaDrumClass };

type BuildResult = {
  staveNotes: StaveNote[];
  tuplets: Tuplet[];
  /** Parallel to staveNotes: the bar-position + lead instrument of each column. */
  positions: PositionInfo[];
  /** Invisible rests holding the silent lead-in before the first hit (may be empty). */
  leadingRests: InvisibleRest[];
  /** Runs of ≥2 beamable notes that fall within the same beat — one Beam each. */
  beamGroups: StaveNote[][];
};

/** True for durations that get beamed (8th and shorter), including dotted. */
function isBeamable(durationCode: string): boolean {
  const base = durationCode.replace(/d+$/, '');
  return base === '8' || base === '16' || base === '32';
}

type VoiceBuild = {
  /** Notes + rests, in order, tiling the whole bar. */
  tickables: StaveNote[];
  /** The note (non-rest) columns, for x-capture + hit-testing. */
  noteColumns: { note: StaveNote; position: number; instrument: SchemaDrumClass }[];
  beamGroups: StaveNote[][];
};

/** A rest glyph on the middle line. `code` is a VexFlow rest duration ('qr', '8r'…). */
function restNote(code: string): StaveNote {
  return new StaveNote({ keys: ['b/4'], duration: code });
}

/**
 * Build a fully readable bar: walk each beat and emit a note at every hit and a
 * rest for every gap (leading, between hits, trailing), so silence is notated
 * rather than left blank. Rests stay within a beat (beat-capped durations
 * guarantee it) and decompose into standard values. Beams join consecutive
 * notes within a beat, broken by rests. A completely empty bar gets one whole
 * rest. (Non-tuplet bars only — the tuplet path stays on buildStaveNotes.)
 */
function buildBarVoice(
  notes: readonly Note[],
  maxSixteenth: number,
  sixteenthsPerBeat: number,
): VoiceBuild {
  const bySlot = new Map<number, Note[]>();
  for (const note of notes) {
    const slot = Math.round(parsePosition(note.position) * 16);
    if (slot < 0 || slot >= maxSixteenth) continue;
    const list = bySlot.get(slot);
    if (list) list.push(note);
    else bySlot.set(slot, [note]);
  }

  if (bySlot.size === 0) {
    return { tickables: [restNote('wr')], noteColumns: [], beamGroups: [] };
  }

  const tickables: StaveNote[] = [];
  const noteColumns: VoiceBuild['noteColumns'] = [];
  const beamGroups: StaveNote[][] = [];
  let run: StaveNote[] = [];
  const flush = (): void => {
    if (run.length >= 2) beamGroups.push(run);
    run = [];
  };

  // Metric rest decomposition: emit the largest standard rest that fits AND is
  // aligned to its own value from the beat start (a value of size N must begin
  // on a multiple of N). This keeps rests reading correctly — e.g. a silence
  // from the "e" to the next beat renders 16th + 8th, not 8th + 16th.
  const restCode: Record<number, string> = { 16: 'wr', 8: 'hr', 4: 'qr', 2: '8r', 1: '16r' };
  const restSizes: number[] = [];
  for (let s = sixteenthsPerBeat; s >= 1; s = Math.floor(s / 2)) restSizes.push(s);
  const emitRests = (from: number, to: number, beatStart: number): void => {
    flush(); // a rest breaks any running beam
    let pos = from;
    while (pos < to) {
      let size = 1;
      for (const cand of restSizes) {
        if (cand <= to - pos && (pos - beatStart) % cand === 0) {
          size = cand;
          break;
        }
      }
      tickables.push(restNote(restCode[size]!));
      pos += size;
    }
  };

  const numBeats = Math.round(maxSixteenth / sixteenthsPerBeat);
  for (let b = 0; b < numBeats; b++) {
    const beatStart = b * sixteenthsPerBeat;
    const beatEnd = beatStart + sixteenthsPerBeat;
    const slots = [...bySlot.keys()]
      .filter((s) => s >= beatStart && s < beatEnd)
      .sort((a, b) => a - b);
    let cursor = beatStart;
    for (let k = 0; k < slots.length; k++) {
      const s = slots[k]!;
      if (s > cursor) emitRests(cursor, s, beatStart);
      const nextOnset = k + 1 < slots.length ? slots[k + 1]! : beatEnd;
      const dur16 = nextOnset - s;
      const hits = bySlot.get(s)!;
      const code = gapToDuration(dur16);
      const note = new StaveNote({ keys: hits.map(keyForHit), duration: code });
      note.setStemDirection(Stem.UP);
      if (code.includes('d')) Dot.buildAndAttach([note], { all: true });
      tickables.push(note);
      noteColumns.push({ note, position: s / 16, instrument: hits[0]!.instrument });
      cursor = s + dur16;
      if (isBeamable(code)) run.push(note);
      else flush();
    }
    if (cursor < beatEnd) emitRests(cursor, beatEnd, beatStart);
    flush(); // beat boundary breaks the beam
  }

  return { tickables, noteColumns, beamGroups };
}

/**
 * Greedily decompose `sixteenths` (1/16ths of a whole note) into invisible
 * rests so the formatter reserves that much horizontal space. Used to push the
 * first hit of a bar to its true position when the bar doesn't start on the
 * downbeat (e.g. the drums entering mid-bar). Uses only undotted values — the
 * rests render nothing, so only the total tick count matters.
 */
function makeLeadingRests(sixteenths: number): InvisibleRest[] {
  const table: [number, string][] = [
    [16, 'w'], [8, 'h'], [4, 'q'], [2, '8'], [1, '16'],
  ];
  const rests: InvisibleRest[] = [];
  let rem = Math.max(0, Math.round(sixteenths));
  for (const [value, code] of table) {
    while (rem >= value) {
      rests.push(new InvisibleRest({ duration: code }));
      rem -= value;
    }
  }
  return rests;
}

function buildStaveNotes(
  notes: readonly Note[],
  barLengthInSixteenths: number,
  sixteenthsPerBeat: number,
): BuildResult {
  // Group hits at the same position into one chord (one StaveNote, multi-key).
  const byPosition = new Map<string, Note[]>();
  for (const note of notes) {
    const list = byPosition.get(note.position) ?? [];
    list.push(note);
    byPosition.set(note.position, list);
  }

  const sortedPositions = [...byPosition.keys()].sort(
    (a, b) => parsePosition(a) - parsePosition(b),
  );

  // Silent lead-in: if the first hit isn't on the downbeat, reserve that time
  // with invisible rests so notes land at their true metric x.
  const firstOffset16 =
    sortedPositions.length > 0 ? Math.round(parsePosition(sortedPositions[0]!) * 16) : 0;
  const leadingRests = makeLeadingRests(firstOffset16);

  // Tracking which StaveNotes belong to which tuplet group, with the
  // numNotes/notesOccupied ratio carried per group.
  type Membership = { groupId: string; numNotes: number; notesOccupied: number };
  const memberships: (Membership | null)[] = [];
  const staveNotes: StaveNote[] = [];
  const positions: PositionInfo[] = [];
  const durations: string[] = [];

  for (let i = 0; i < sortedPositions.length; i++) {
    const pos = sortedPositions[i]!;
    const hits = byPosition.get(pos);
    if (!hits || hits.length === 0) continue;

    // Tuplet detection: if any hit at this position carries a `tuplet` field,
    // the position is part of that tuplet group.
    const tupletHit = hits.find((h) => h.tuplet);
    const tupletInfo = tupletHit?.tuplet;

    let duration: string;
    if (tupletInfo && tupletHit) {
      // Tuplet members use the schema's *written* duration; the Tuplet wrapper
      // handles the visual scaling.
      duration = VF_DURATION[tupletHit.duration];
    } else {
      // Non-tuplet: written value = time to the next hit, but capped at this
      // note's beat boundary so a value never spills across a beat line. Without
      // the cap, a lone hit near a beat's end (or the bar's last hit) becomes a
      // half/dotted value that spans beats, which breaks beat-grouped beaming
      // (VexFlow won't beam a beat-crossing note) and misrepresents the rhythm.
      const thisIn16 = Math.round(parsePosition(pos) * 16);
      const nextIn16 = i + 1 < sortedPositions.length
        ? Math.round(parsePosition(sortedPositions[i + 1]!) * 16)
        : barLengthInSixteenths;
      const beatBoundary = (Math.floor(thisIn16 / sixteenthsPerBeat) + 1) * sixteenthsPerBeat;
      const gap = Math.max(1, Math.min(nextIn16, beatBoundary) - thisIn16);
      duration = gapToDuration(gap);
    }

    const keys = hits.map(keyForHit);
    const staveNote = new StaveNote({ keys, duration });
    // setStemDirection AFTER construction — the constructor's stemDirection
    // field is silently ignored due to a snake_case/camelCase mismatch
    // between VexFlow 5's runtime and its TypeScript types.
    staveNote.setStemDirection(Stem.UP);
    // VexFlow counts the dot in the note's ticks but doesn't draw the dot glyph
    // unless a Dot modifier is attached. Attach one per notehead for dotted
    // durations so the augmentation dot renders.
    if (duration.includes('d')) {
      Dot.buildAndAttach([staveNote], { all: true });
    }
    staveNotes.push(staveNote);
    positions.push({ value: parsePosition(pos), instrument: hits[0]!.instrument });
    durations.push(duration);

    memberships.push(
      tupletInfo
        ? {
            groupId: tupletInfo.group,
            numNotes: tupletInfo.actual,
            notesOccupied: tupletInfo.normal,
          }
        : null,
    );
  }

  // Group StaveNotes by tuplet.group → one Tuplet bracket per group.
  type GroupAccumulator = { notes: StaveNote[]; numNotes: number; notesOccupied: number };
  const byGroupId = new Map<string, GroupAccumulator>();
  for (let i = 0; i < staveNotes.length; i++) {
    const m = memberships[i];
    if (!m) continue;
    const existing = byGroupId.get(m.groupId);
    if (existing) {
      existing.notes.push(staveNotes[i]!);
    } else {
      byGroupId.set(m.groupId, {
        notes: [staveNotes[i]!],
        numNotes: m.numNotes,
        notesOccupied: m.notesOccupied,
      });
    }
  }

  const tuplets: Tuplet[] = [];
  for (const { notes: tupletNotes, numNotes, notesOccupied } of byGroupId.values()) {
    tuplets.push(
      new Tuplet(tupletNotes, { numNotes, notesOccupied }),
    );
  }

  // Beam grouping: group consecutive beamable columns that share a beat (drum
  // notation beams by beat). A note whose written value spills past its beat's
  // boundary still beams with its beat-mates here — VexFlow's default
  // generateBeams refuses to beam across a beat line, which is exactly why two
  // eighths within a beat wouldn't beam when one carried a boundary-crossing
  // (e.g. dotted) duration.
  const beamGroups: StaveNote[][] = [];
  let run: StaveNote[] = [];
  let runBeat = -1;
  const flushRun = (): void => {
    if (run.length >= 2) beamGroups.push(run);
    run = [];
    runBeat = -1;
  };
  for (let i = 0; i < staveNotes.length; i++) {
    const beat = Math.floor(Math.round(positions[i]!.value * 16) / sixteenthsPerBeat);
    if (isBeamable(durations[i]!) && (run.length === 0 || beat === runBeat)) {
      run.push(staveNotes[i]!);
      runBeat = beat;
    } else {
      flushRun();
      if (isBeamable(durations[i]!)) {
        run.push(staveNotes[i]!);
        runBeat = beat;
      }
    }
  }
  flushRun();

  return { staveNotes, tuplets, positions, leadingRests, beamGroups };
}

function keyForHit(hit: Note): string {
  const mapping = DRUM_POSITION[hit.instrument];
  // Suffix `/x2` selects VexFlow's X notehead for cymbals + hi-hat hits.
  return mapping.xNotehead ? `${mapping.key}/x2` : mapping.key;
}

export function parsePosition(p: string): number {
  if (p === '0') return 0;
  const slash = p.indexOf('/');
  if (slash === -1) return Number(p);
  return Number(p.slice(0, slash)) / Number(p.slice(slash + 1));
}

/**
 * Format a whole-note fraction as a schema-valid reduced position string.
 * `sixteenths` is the position expressed in 1/16ths of a whole note (an integer
 * after snapping); e.g. 4 → "1/4", 0 → "0", 16 → "1", 6 → "3/8".
 */
export function formatPosition(sixteenths: number): string {
  const n = Math.max(0, Math.round(sixteenths));
  if (n === 0) return '0';
  const g = gcd(n, 16);
  const num = n / g;
  const den = 16 / g;
  return den === 1 ? String(num) : `${num}/${den}`;
}

function gcd(a: number, b: number): number {
  return b === 0 ? a : gcd(b, a % b);
}
