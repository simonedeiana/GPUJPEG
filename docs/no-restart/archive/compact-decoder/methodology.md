> Archived investigation document. See the [current branch guide](../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# Compact canonical Huffman and parallel unstuffing experiment

The main comparison uses the same ten exact JPEGs as the earlier staged-decoder
experiment. Each process repeatedly decodes one memory-resident JPEG and returns
8-bit RGB or grayscale pixels to CPU memory. A PPM/PGM output, exact dimensions,
channel count, pixel byte count and SHA256 are required. No Fastvideo `-discard`
option is used. The no-restart inputs exercise the existing CPU fallback in
GPUJPEG; enabling these GPU flags does not replace that fallback.

## Primary latency

GPUJPEG's `Decode Image:` host timer starts before JPEG parsing and stops after
synchronous output download. For the exact investigated Fastvideo EXE, the
observer records absolute QPC timestamps without changing the original file.
Its primary latency is:

```
1000 * (decode.stop - header_load.start) / QPC_frequency  [milliseconds]
```

This continuous interval includes memory-stream header/SOS parsing, compressed
tail copying, intervening host setup, codec work, H2D and completed D2H. The
Fastvideo printed header-load timer contains an additional cached initial disk
read charge, so printed header-load + decode + D2H is retained only as an INVALID
diagnostic and never used in the main comparison. Printed codec-only timing and
whole-process wall time remain separately labeled secondary measurements.
Native addresses, timer formulas, synchronous-download proof and observer
overhead calibration are in `native-timer-proof.md` and `qpc-overhead.txt`.

Every Fastvideo process must produce exactly two alternating, monotonic L/D
records per frame, from one thread with a positive QPC frequency equal to Windows
QueryPerformanceFrequency. The EXE SHA256 is enforced before loading the
version-specific observer. The observer buffers records in RAM and writes at
process detach; it subscribes to no CUDA callbacks. Original binaries and
JPEGs are checked before and after execution.

## Scheduling and attribution

The main run has five rounds, ten images, four variants and 110 frames per
process, discarding the first ten frames: 200 processes and 500 measured frames
per image/variant. The headline is the median of the five per-round medians.
Variants rotate order by round, and image order reverses on alternate rounds.
All child processes run serially during an exclusive GPU window. WDDM clocks,
thermal state and host scheduling are not locked; round medians and complete
observations are retained rather than implying controlled hardware clocks.

The ablation uses four core restart-10 JPEGs, three rounds, 60 frames and ten
warmups, comparing fused control plus four builds of the SAME frozen source
revision: staged flags OFF/OFF, compact-only ON/OFF, unstuff-only OFF/ON, and
combined ON/ON. This is a 2x2 feature comparison with a fused control, totaling
60 processes. A historical staged binary is optional and must not be used to
attribute changes caused by a reader refactor in the current revision.

Unstuff-only is promoted to a fourth main variant after the ablation showed
nonoverlapping HD420 round ranges versus combined and fused. Other core images
did not show a consistent benefit, so no general speedup is assumed.

Result label `staged` in the main run means the NEW combined candidate. The
binary's actual CMake options and SHA256 are embedded in `plan.json`; the label
does not mean the previous experiment's staged binary.

## Trace corroboration and kernels

Sixteen Nsight traces cover HD/4K 4:4:4 and 4:2:0 restart-10 inputs through all
four main variants, 30 frames each with ten warmups. A trace window starts at
the first payload-sized H2D device activity after the preceding output and ends
at the full output-sized D2H completion. This is a secondary GPU transfer-window
diagnostic; CPU work before upload is excluded and profiling perturbs timing.
The exact entropy payload minimum excludes restart and EOI markers already
removed by GPUJPEG's CPU reader, while retaining stuffed FF00 pairs.

Each trace must contain exactly 30 expected-size D2H copies. Required compact
Huffman and parallel-unstuff kernel patterns are asserted on every warm
combined-candidate frame. Unstuff-only must execute its prepass and token parser
with COMPACT=false and UNSTUFFED=true on every warm frame. `kernel-summary.json` reports, per exact demangled
kernel name, median sum of launch durations per frame, median launch duration,
frames present, and all observed launch resources. Grid, block dimensions,
registers/thread, shared-memory bytes and local-memory values come directly
from SQLite; these are launch metadata, not occupancy or memory-traffic
measurements. Per-frame records retain every launch and its duration.

## Portable entry points

Use Python 3 and the original Fastvideo executable plus locally built GPUJPEG
binaries. The manifest accepts relative JPEG paths resolved beside the manifest.
Use `benchmark.py --help` and `profile_core.py --help` for path arguments.
For the main run supply `--candidate-label staged`; for the ablation supply
`--candidate-label combined`, three `--extra label=path` builds and five explicit
`--variant` selections to omit Fastvideo. Profiling additionally requires Nsight
Systems and `--require-kernel LABEL=REGEX` patterns matching the final symbols in each experimental variant.
`--dry-run` writes planned commands and metadata without starting GPU work.
`finalize_qpc.py RESULT_DIRECTORY` can independently recompute the continuous
primary metric from retained immutable raw TSV counters; ordinary current
benchmark runs already apply that formula directly.
