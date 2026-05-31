import { renderScore } from './render';
async function init() {
    const response = await fetch('./events.json');
    if (!response.ok) {
        throw new Error(`Failed to fetch events.json: ${response.status} ${response.statusText}`);
    }
    const events = (await response.json());
    const meta = document.getElementById('meta');
    if (meta) {
        const ts = events.time_signature;
        meta.textContent = `${events.tempo_bpm.toFixed(1)} BPM · ${ts.numerator}/${ts.denominator} · ${events.bars.length} bars · ${events.audio_file ?? '(no audio_file)'}`;
    }
    const score = document.getElementById('score');
    if (score)
        renderScore(score, events);
    const raw = document.getElementById('raw');
    if (raw)
        raw.textContent = JSON.stringify(events, null, 2);
}
init().catch((err) => {
    const main = document.querySelector('main');
    const msg = err instanceof Error ? err.message : String(err);
    if (main) {
        main.innerHTML = `<p style="color:red">Failed to load: ${msg}</p>`;
    }
    console.error(err);
});
