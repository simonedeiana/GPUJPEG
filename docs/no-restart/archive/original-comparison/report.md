> Archived investigation document. See the [current branch guide](../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# Original GPUJPEG versus supplied Fastvideo: direct decode comparison

This fresh run compares the **unmodified GPUJPEG Release executable built from
upstream commit `78e5eee3f75e0a2a2e6c1409000ac05d679b4af8`**, before any table
cache or staged-decoder work, against the unchanged supplied Fastvideo 0.21.1.0
trial executable. The GPUJPEG build is the original `work/build-baseline`
artifact from 17:39 on 2026-10-20; it was not rebuilt from the subsequently
modified working tree. Binary SHA-256 identities are in `plan.json`.

The preceding staged/compact comparison instead used the fused GPUJPEG build
with the added table cache. Its headline values must not be described as a
strict comparison between two untouched programs. This direct run closes that
gap; original-versus-original figures below are new measurements.

## Results

RTX 3050 8 GB, SM86, driver 617.42, Windows WDDM, CUDA 13.4, one application
thread. All codecs consume identical JPEGs and return RGB8/gray8 pixels to CPU
memory. Times include parsing, CPU preparation, H2D, decoding and completed D2H;
initial input/final output disk I/O and process startup are excluded.

Five rounds rotate decoder order and reverse image order on alternate rounds.
Each child decodes 110 times and discards the first ten samples. Each cell is
the median of five per-run medians (500 retained frames per image/decoder).
There are 100 successful child runs and 10,000 retained warm frames.
Clocks are unlocked; the small corpus uses multiple encodings of a few scenes.

| JPEG Q90 | Original GPUJPEG ms | Fastvideo ms | Lower median |
|---|---:|---:|---|
| hd444-r10 | 2.574 | 2.466 | Fastvideo |
| hd420-r10 | 3.193 | 3.101 | Fastvideo |
| hd422-r10 | 2.671 | 2.591 | Fastvideo |
| 4k444-r10 | 7.382 | 6.971 | Fastvideo |
| 4k420-r10 | 6.737 | 7.017 | GPUJPEG |
| hdgray-r10 | 1.394 | 1.300 | Fastvideo |
| hd444-r1 | 3.316 | 2.094 | Fastvideo |
| hd444-r32 | 3.354 | 3.500 | GPUJPEG |
| hd444-r0 | 20.040 | 29.980 | GPUJPEG |
| 4k420-r0 | 44.077 | 62.690 | GPUJPEG |

Fastvideo has the lower median on 6 of ten inputs; GPUJPEG
has it on 4. A lower median is not a statistical-significance
claim. No-restart files (R0) use CPU entropy fallback in original GPUJPEG, while
Fastvideo performs CPU restart preparation before its GPU pipeline. Despite
that different route, the whole-codec intervals include both programs' work.

## Timing and provenance

GPUJPEG uses its continuous `Decode Image` host timer. Fastvideo uses the
version-bound QPC observer already validated in the preceding investigation:
continuous header-load start through completed codec/download stop. Its printed
JFIF-load value adds cached initial disk I/O and cannot simply be summed with
printed decode/download times. No Fastvideo `-discard` flag was used.
The unchanged executable and JPEG bytes are hashed before/after; the observer
changes only the spawned process's QPC import pointer. Calibration estimated
0.039–0.046 microseconds per frame, not subtracted from the measurements.
[Full timer methodology](../compact-decoder/native-timer-proof.md) records the
native proof and exact scope.

GPUJPEG's new-run output pixel hashes were checked against the existing fused
reference for all ten images; every pixel hash is identical. Fastvideo's hashes
also match its previous validated outputs. This run changes no codec source.

## Round ranges

Minimum–maximum of five warm run medians, not confidence intervals:

| Input | GPUJPEG round range ms | Fastvideo round range ms |
|---|---:|---:|
| hd444-r10 | 2.516–2.646 | 2.385–2.540 |
| hd420-r10 | 3.168–3.248 | 2.993–3.115 |
| hd422-r10 | 2.640–2.703 | 2.571–2.646 |
| 4k444-r10 | 7.361–7.398 | 6.890–6.980 |
| 4k420-r10 | 6.686–6.803 | 6.837–7.119 |
| hdgray-r10 | 1.375–1.502 | 1.273–1.328 |
| hd444-r1 | 3.284–3.424 | 2.077–2.151 |
| hd444-r32 | 3.339–3.377 | 3.467–3.760 |
| hd444-r0 | 19.496–20.227 | 28.804–30.675 |
| 4k420-r0 | 43.590–44.795 | 61.262–64.480 |

The files beside this report retain commands, full samples, round medians,
raw console/QPC records and before/after hashes. The experiment does not provide
an equally controlled original-versus-original **encoding** comparison.
