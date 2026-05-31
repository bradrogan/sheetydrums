import { Renderer, Stave, StaveNote, Voice, Formatter, Stem, Beam, Tuplet } from 'vexflow';
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

export function renderScore(
  container: HTMLElement,
  events: DrumTranscriptionEventsV1Draft,
): void {
  container.innerHTML = '';

  const tsLabel = `${events.time_signature.numerator}/${events.time_signature.denominator}`;
  for (const [i, bar] of events.bars.entries()) {
    const isFirstBar = i === 0;
    renderBar(container, bar, tsLabel, isFirstBar);
  }
}

function renderBar(
  container: HTMLElement,
  bar: DrumTranscriptionEventsV1Draft['bars'][number],
  timeSig: string,
  showClefAndTimeSig: boolean,
): void {
  const row = document.createElement('div');
  row.className = 'bar-row';

  const label = document.createElement('div');
  label.className = 'bar-label';
  label.textContent = `Bar ${bar.index}`;
  row.appendChild(label);

  const svgHost = document.createElement('div');
  svgHost.className = 'bar-svg';
  row.appendChild(svgHost);
  container.appendChild(row);

  const renderer = new Renderer(svgHost, Renderer.Backends.SVG);
  renderer.resize(BAR_SVG_WIDTH, BAR_SVG_HEIGHT);
  const ctx = renderer.getContext();

  const stave = new Stave(STAVE_X, STAVE_Y, STAVE_WIDTH);
  if (showClefAndTimeSig) {
    stave.addClef('percussion').addTimeSignature(timeSig);
  }
  stave.setContext(ctx).draw();

  const [num, den] = timeSig.split('/').map(Number);
  // Bar length expressed in 1/16ths of a whole note.
  // (numerator / denominator) gives the bar in whole notes; × 16 gives 16ths.
  const barLengthInSixteenths = Math.round(((num ?? 4) / (den ?? 4)) * 16);

  const { staveNotes, tuplets } = buildStaveNotes(bar.notes, barLengthInSixteenths);
  if (staveNotes.length === 0) return;

  // strict=false lets the rendered voice be flexible about exact totals
  // (real transcribed bars rarely sum to a perfect whole-note worth of duration).
  const voice = new Voice({ numBeats: num ?? 4, beatValue: den ?? 4 });
  voice.setStrict(false);
  voice.addTickables(staveNotes);

  // Generate beams BEFORE drawing the voice. Beam construction marks each
  // constituent note as "in a beam," and StaveNote checks that flag during
  // draw() to skip its own stem-flag. If we drew the voice first, every note
  // would draw its individual flag and the beam would overlay on top.
  // `stemDirection: Stem.UP` aligns beam-side with our stems-up convention.
  const beams = Beam.generateBeams(staveNotes, { stemDirection: Stem.UP });

  new Formatter().joinVoices([voice]).format([voice], STAVE_WIDTH - 80);
  voice.draw(ctx, stave);

  for (const beam of beams) {
    beam.setContext(ctx).draw();
  }
  // Tuplet brackets/labels drawn last so they sit above the beams.
  for (const tuplet of tuplets) {
    tuplet.setContext(ctx).draw();
  }
}

type BuildResult = {
  staveNotes: StaveNote[];
  tuplets: Tuplet[];
};

function buildStaveNotes(
  notes: readonly Note[],
  barLengthInSixteenths: number,
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

  // Tracking which StaveNotes belong to which tuplet group, with the
  // numNotes/notesOccupied ratio carried per group.
  type Membership = { groupId: string; numNotes: number; notesOccupied: number };
  const memberships: (Membership | null)[] = [];
  const staveNotes: StaveNote[] = [];

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
      // Non-tuplet: duration = time-to-next-hit in 16ths.
      const thisIn16 = Math.round(parsePosition(pos) * 16);
      const nextIn16 = i + 1 < sortedPositions.length
        ? Math.round(parsePosition(sortedPositions[i + 1]!) * 16)
        : barLengthInSixteenths;
      const gap = Math.max(1, nextIn16 - thisIn16);
      duration = gapToDuration(gap);
    }

    const keys = hits.map(keyForHit);
    const staveNote = new StaveNote({ keys, duration });
    // setStemDirection AFTER construction — the constructor's stemDirection
    // field is silently ignored due to a snake_case/camelCase mismatch
    // between VexFlow 5's runtime and its TypeScript types.
    staveNote.setStemDirection(Stem.UP);
    staveNotes.push(staveNote);

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

  return { staveNotes, tuplets };
}

function keyForHit(hit: Note): string {
  const mapping = DRUM_POSITION[hit.instrument];
  // Suffix `/x2` selects VexFlow's X notehead for cymbals + hi-hat hits.
  return mapping.xNotehead ? `${mapping.key}/x2` : mapping.key;
}

function parsePosition(p: string): number {
  if (p === '0') return 0;
  const slash = p.indexOf('/');
  if (slash === -1) return Number(p);
  return Number(p.slice(0, slash)) / Number(p.slice(slash + 1));
}
