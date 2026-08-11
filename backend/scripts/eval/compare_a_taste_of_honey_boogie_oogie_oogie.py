"""Compare an events.json against the ground-truth for A Taste Of Honey - "Boogie Oogie Oogie".

Usage (from backend/):
    uv run sheetydrums "<youtube url>" -o /tmp/boogie.json
    uv run python scripts/eval/compare_a_taste_of_honey_boogie_oogie_oogie.py /tmp/boogie.json --full-vocab

Why this song: it is the closed-dominant, high-tempo stress case for the
CheukExpander hi-hat split. Donald Johnson plays relentless SIXTEENTH-note hats
(~90% of the song is closed 16ths) at 124 bpm -- 16ths are ~0.12 s apart, so the
expander's fixed 0.15-0.35 s "sustain" window straddles the *next* one-to-two
strokes. That aliasing is exactly what pushes a busy closed groove over the
"unimodal open" threshold and flips the whole song to open. The choruses then add
a genuine open-hat passage (disco offbeat "barks") so open recall is also tested.

Ground-truth provenance & confidence:
  Transcribed from the Drumeo transcription (Mathias Hedegaard Nielsen), 124 bpm,
  4/4, ~148 bars. The **hi-hat mode per section is authoritative** (closed 16ths
  vs the alternating open-16th chorus pattern, read at high zoom). Two deliberate
  simplifications, both conservative for the over-firing measurement:
    - closed sections are encoded as fully-closed 16ths; the occasional single
      phrase-end open "bark" seen in some intro/verse bars is omitted (counts as
      closed -> at worst a few open false-negatives, never a false open).
    - kick = four-on-the-floor, snare = backbeat 2 & 4 (the song's foundation,
      enough for DP bar-alignment, not a note-perfect kick/snare transcription).
  Fills are approximated by the section groove. The reference PDF is Drumeo
  "Licensed For Personal Use Only" and is NOT committed -- only these derived
  per-section patterns live in the repo.

Section map (bar numbers from the chart's system labels; ~148 bars):
    Intro           1-24    closed 16ths ("Play 3x")
    Verse 1         25-40   closed 16ths
    Chorus 1 lick   41-44   open 16ths (offbeat barks)
    Chorus 1 body   45-60   closed 16ths
    Verse 2         61-72   closed 16ths
    Chorus 2 lick   73-76   open 16ths
    Chorus 2 body   77-84   closed 16ths
    Breakdown       85-116  closed 16ths
    Chorus 3        117-132 open 16ths (long outro chorus)
    Outro           133-144 closed 16ths ("Play 3x", fade out)
    Outro open      145-148 open 16ths
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import parse_row, run_report  # noqa: E402


SOURCE = 'A Taste Of Honey - "Boogie Oogie Oogie" (1978), 124 bpm 4/4 -- pass the audio URL to the pipeline'

# 16th-note hi-hats fill every cell. Closed mode = all x. Open (chorus) mode =
# closed on the 8th-note positions (beat + "&") and open on the "e"/"a" 16ths.
HAT_CLOSED = "xxxxxxxxxxxxxxxx"
HAT_OPEN = "xXxXxXxXxXxXxXxX"
KICK = "o---o---o---o---"       # four-on-the-floor
SNARE = "----o-------o---"      # backbeat 2 & 4


def _groove(hat: str, n: int) -> list[dict[str, set[int]]]:
    text = (
        "H-|" + "|".join([hat] * n) + "|\n"
        "S-|" + "|".join([SNARE] * n) + "|\n"
        "B-|" + "|".join([KICK] * n) + "|"
    )
    return parse_row(text)


def build_tab() -> tuple[list[dict[str, set[int]]], list[tuple[str, int]]]:
    flat: list[dict[str, set[int]]] = []
    sections: list[tuple[str, int]] = []

    def section(name: str, hat: str, n: int) -> None:
        sections.append((name, len(flat)))
        flat.extend(_groove(hat, n))

    section("Intro (closed)", HAT_CLOSED, 24)         # 1-24
    section("Verse 1 (closed)", HAT_CLOSED, 16)       # 25-40
    section("Chorus 1 lick (open)", HAT_OPEN, 4)      # 41-44
    section("Chorus 1 body (closed)", HAT_CLOSED, 16) # 45-60
    section("Verse 2 (closed)", HAT_CLOSED, 12)       # 61-72
    section("Chorus 2 lick (open)", HAT_OPEN, 4)      # 73-76
    section("Chorus 2 body (closed)", HAT_CLOSED, 8)  # 77-84
    section("Breakdown (closed)", HAT_CLOSED, 32)     # 85-116
    section("Chorus 3 (open)", HAT_OPEN, 16)          # 117-132
    section("Outro (closed)", HAT_CLOSED, 12)         # 133-144
    section("Outro open", HAT_OPEN, 4)                # 145-148

    return flat, sections


if __name__ == "__main__":
    sys.exit(run_report(build_tab, SOURCE))
