import { Renderer, Stave, StaveNote, Voice, Formatter } from 'vexflow';
import type { DrumTranscriptionEventsV1Draft } from './generated/events';

/**
 * Scaffold renderer: shows a count summary and draws a basic VexFlow stave
 * with four quarter-notes — enough to prove the lib is loaded and rendering.
 *
 * Real drum-sheet rendering (percussion clef, per-instrument staff positions,
 * stem direction by voice, open/closed hi-hat symbols, etc.) is a follow-on.
 */
export function renderScore(container: HTMLElement, events: DrumTranscriptionEventsV1Draft): void {
  container.innerHTML = '';

  const totalNotes = events.bars.reduce((sum, b) => sum + b.notes.length, 0);

  const summary = document.createElement('div');
  summary.className = 'score-summary';
  summary.textContent = `${totalNotes} notes across ${events.bars.length} bars. Percussion-staff rendering not yet implemented; the stave below is a VexFlow smoke test.`;
  container.appendChild(summary);

  const staveEl = document.createElement('div');
  staveEl.id = 'vf-stave';
  container.appendChild(staveEl);

  const renderer = new Renderer(staveEl, Renderer.Backends.SVG);
  renderer.resize(500, 200);
  const context = renderer.getContext();

  const stave = new Stave(10, 40, 460);
  stave.addClef('treble').addTimeSignature('4/4');
  stave.setContext(context).draw();

  const notes = [
    new StaveNote({ keys: ['c/5'], duration: 'q' }),
    new StaveNote({ keys: ['d/5'], duration: 'q' }),
    new StaveNote({ keys: ['e/5'], duration: 'q' }),
    new StaveNote({ keys: ['f/5'], duration: 'q' }),
  ];

  const voice = new Voice({ numBeats: 4, beatValue: 4 });
  voice.addTickables(notes);

  new Formatter().joinVoices([voice]).format([voice], 400);
  voice.draw(context, stave);
}
