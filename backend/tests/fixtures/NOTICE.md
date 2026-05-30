# Test fixtures — third-party content

Audio files committed under this directory are third-party works used as test
inputs for the sheetydrums pipeline. Each file is listed below with its
source, author, and license. Anyone distributing this repository must
preserve this NOTICE alongside the fixtures.

---

## `eric-keyes-lost.ogg`

- **Title**: Lost
- **Author**: Eric Keyes (1996)
- **Source**: Wikimedia Commons
- **File page**: <https://commons.wikimedia.org/wiki/File:Eric_Keyes_Performing_%22Lost%22.ogg>
- **Direct URL**: <https://upload.wikimedia.org/wikipedia/commons/0/08/Eric_Keyes_Performing_%22Lost%22.ogg>
- **License**: Creative Commons Attribution-Share Alike 3.0 Unported
  (<https://creativecommons.org/licenses/by-sa/3.0/>)
- **Duration**: 30.07 seconds
- **Format**: Ogg Vorbis, stereo, 44.1 kHz, ~143 kbps
- **Why this file**: ~30-second full-mix rock performance. Demucs separates a
  drum stem at ~57% of input RMS energy, indicating prominent drums — useful
  for verifying drum-transcription stages downstream of Demucs.

### License compliance

The file is included unmodified for software-testing purposes. Under
CC-BY-SA 3.0:

- **Attribution**: Credit Eric Keyes, link to the source page above, and
  indicate that the file is used here unmodified.
- **ShareAlike**: Any derivative work that incorporates this fixture must be
  released under CC-BY-SA 3.0 or a compatible license. This requirement does
  **not** extend to the sheetydrums source code itself — only to fixtures
  derived from this file (e.g. trimmed versions, mixed-in test inputs).
