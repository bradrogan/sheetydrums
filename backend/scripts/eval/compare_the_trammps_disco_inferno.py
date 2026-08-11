"""Compare an events.json against the ground-truth for The Trammps - "Disco Inferno".

Usage (from backend/):
    uv run sheetydrums "<youtube url>" -o /tmp/disco.json
    uv run python scripts/eval/compare_the_trammps_disco_inferno.py /tmp/disco.json --full-vocab

Why this song: Earl Young's groove here is the canonical open/closed hi-hat test.
The chart genuinely spans the whole range, section by section:
  - closed verses               (plain x)          -> hihat_closed only
  - grooves with offbeat opens   (x with X on &2/&4) -> mostly closed, few opens
  - an all-open bridge + last chorus (circled X)     -> hihat_open throughout
So it exercises both failure directions of the CheukExpander hi-hat split at once
(over-firing open on closed verses; missing opens on the all-open bridge).

Ground-truth provenance & confidence:
  Transcribed from the Drumeo transcription (Mathias Hedegaard Nielsen), 129 bpm,
  4/4, 92 bars. The **hi-hat open/closed character per section is authoritative**
  (each section's noteheads were read at high zoom: plain-x vs circled-x). Kick is
  encoded as four-on-the-floor and snare as the backbeat 2 & 4 -- the song's
  foundation, accurate enough for DP bar-alignment but NOT a note-perfect kick/snare
  transcription. Intro fills and end-of-section fills are approximated by the section
  groove (plus a 2-bar snare roll in the intro). Do not treat kick/snare F1 here as a
  precise accuracy figure; the hi-hat columns are the ones this song exists to measure.

  The reference PDF is Drumeo "Licensed For Personal Use Only" -- it is NOT committed;
  only these derived per-section hit patterns live in the repo.

Section map (bar numbers from the chart's system labels):
    Intro                 1-7    fill: crash pickup + 2-bar snare roll, then closed groove
    Intro groove          8-15   closed
    Groove (offbeat open) 16-23  open on &2 and &4
    Verse 1               24-27  closed
    Verse 1 cont          28-35  closed
    Chorus 1              36-43  open on &2 and &4
    Verse 2               44-47  closed
    Verse 2 cont          48-55  closed
    Chorus 2              56-63  open on &2 and &4
    Bridge                64-72  all open (+ hi-hat foot, not scored)
    Verse 3               73-84  closed
    Chorus 3              85-92  all open  ("Play 4x", fade out)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import parse_row, run_report  # noqa: E402


SOURCE = 'The Trammps - "Disco Inferno" (1976), 129 bpm 4/4 -- pass the audio URL to the pipeline'

# One 4/4 bar = 16 sixteenth cells; eighth-note hats land on even cells, with a
# beat-1 rest matching the chart. Opens are on the "&" of 2 (cell 6) and 4 (cell 14).
HAT_CLOSED = "--x-x-x-x-x-x-x-"
HAT_OFFOPEN = "--x-x-X-x-x-x-X-"
HAT_ALLOPEN = "--X-X-X-X-X-X-X-"
KICK = "o---o---o---o---"       # four-on-the-floor
SNARE = "----o-------o---"      # backbeat 2 & 4
ROLL16 = "oooooooooooooooo"     # intro buildup (snare sixteenths)


def _groove(hat: str, n: int) -> list[dict[str, set[int]]]:
    """n copies of a groove bar with the given hi-hat template."""
    text = (
        "H-|" + "|".join([hat] * n) + "|\n"
        "S-|" + "|".join([SNARE] * n) + "|\n"
        "B-|" + "|".join([KICK] * n) + "|"
    )
    return parse_row(text)


def build_tab() -> tuple[list[dict[str, set[int]]], list[tuple[str, int]]]:
    flat: list[dict[str, set[int]]] = []
    sections: list[tuple[str, int]] = []

    def section(name: str, bars: list[dict[str, set[int]]]) -> None:
        sections.append((name, len(flat)))
        flat.extend(bars)

    # Intro (1-7): crash pickup, 2-bar snare roll, then 4 bars of closed groove.
    intro = parse_row(
        "C-|x---------------|\n"
        "B-|o---------------|"
    )
    intro += parse_row(
        "S-|" + "|".join([ROLL16] * 2) + "|\n"
        "B-|" + "|".join([KICK] * 2) + "|"
    )
    intro += _groove(HAT_CLOSED, 4)
    section("Intro", intro)

    section("Intro groove (closed)", _groove(HAT_CLOSED, 8))     # 8-15
    section("Groove (offbeat open)", _groove(HAT_OFFOPEN, 8))    # 16-23
    section("Verse 1 (closed)", _groove(HAT_CLOSED, 4))          # 24-27
    section("Verse 1 cont (closed)", _groove(HAT_CLOSED, 8))     # 28-35
    section("Chorus 1 (offbeat open)", _groove(HAT_OFFOPEN, 8))  # 36-43
    section("Verse 2 (closed)", _groove(HAT_CLOSED, 4))          # 44-47
    section("Verse 2 cont (closed)", _groove(HAT_CLOSED, 8))     # 48-55
    section("Chorus 2 (offbeat open)", _groove(HAT_OFFOPEN, 8))  # 56-63
    section("Bridge (all open)", _groove(HAT_ALLOPEN, 9))        # 64-72
    section("Verse 3 (closed)", _groove(HAT_CLOSED, 12))         # 73-84
    section("Chorus 3 (all open)", _groove(HAT_ALLOPEN, 8))      # 85-92

    return flat, sections


if __name__ == "__main__":
    sys.exit(run_report(build_tab, SOURCE))
