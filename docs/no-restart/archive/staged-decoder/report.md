> Archived investigation document. See the [current branch guide](../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# Staged JPEG decoding: implemented, validated and benchmarked

The three-stage port works, but does **not** reproduce Fastvideo's performance
advantage in GPUJPEG. Keep `HUFFMAN_GPU_STAGED_DECODER` **OFF** by default.
On the tested RTX 3050, HD 4:4:4 R10 takes **2.798 ms staged versus
2.495 ms fused**, with Fastvideo at **2.367 ms**. 4K 4:4:4 R10
takes **8.914 ms staged versus 7.287 ms fused**. These include
input preparation, H2D, decoding and completed output D2H; input/output disk
I/O is excluded. Small differences and fallback results need the variability
and route caveats below.

The final decoder passed **100 default-table + 52 constant-table comparisons**
with exact quantized coefficients and output bytes, plus targeted memcheck,
racecheck and synccheck with zero reported errors/hazards.

## Transfer-inclusive results

Each value is the median of five run medians, each from 100 warm decodes after
discarding 10 warm-ups. There are 150 sequential child runs, 16,500 total decodes
and 15,000 retained warm samples. All codecs use the same JPEG bytes and return
RGB8/gray8 pixels to host RAM. Positive percentages mean the port is slower.

| Input (Q90) | Fused GPUJPEG ms | Staged GPUJPEG ms | Fastvideo ms | Staged vs fused |
|---|---:|---:|---:|---:|
| hd444-r10 | 2.495 | 2.798 | 2.367 | +12.1% |
| hd420-r10 | 3.085 | 3.032 | 3.046 | -1.7% |
| hd422-r10 | 2.643 | 2.959 | 2.652 | +12.0% |
| 4k444-r10 | 7.287 | 8.914 | 6.911 | +22.3% |
| 4k420-r10 | 6.758 | 7.525 | 6.894 | +11.4% |
| hdgray-r10 | 1.402 | 1.490 | 1.294 | +6.3% |
| hd444-r1 | 3.257 | 3.406 | 2.098 | +4.6% |
| hd444-r32 | 3.527 | 3.540 | 3.513 | +0.4% |
| hd444-r0 (CPU fallback) | 19.147 | 18.648 | 29.643 | -2.6% |
| 4k420-r0 (CPU fallback) | 41.476 | 41.849 | 62.735 | +0.9% |

Splitting entropy decoding alone is insufficient here. Most GPU-eligible cases
regress; HD 4:2:0's small apparent improvement is not a demonstrated robust win.
The R0 cases run the unchanged CPU entropy fallback in both GPUJPEG builds,
so their differences are measurement variation, not acceleration by the port.
Fastvideo is also slower on these no-restart files on this host: the data do not
support a universal claim that it beats every other decoder.

## Why the port is slower in the measured HD 4:4:4 case

A separate 40-frame Nsight run on the exact HD444-R10 JPEG gives these kernel
medians. The sum of kernel medians is a stage-cost diagnostic, not an end-to-end
timer or a sum computed from each individual frame.

| Entropy kernel | Fused µs | Staged µs |
|---|---:|---:|
| Fused Huffman/RLE/DC | 494.274 | — |
| Raw Huffman-token parser | — | 536.322 |
| RLE expansion | — | 197.857 |
| DC prefix restoration | — | 36.256 |
| Sum of medians | 494.274 | 770.435 |

The staged parser is already slower; the added token writes/reads, expansion,
DC scan and launches add work. Three bounded RLE designs were profiled; none
removed the regression. The retained shared transpose is a correct experimental
implementation, not evidence that this layout is optimal. Token traffic is a
plausible explanation consistent with the design and profiles; no bandwidth
counter experiment isolates it as the sole cause.

## Independent transfer-window check

These separate Nsight runs measure first compressed-payload H2D device activity
through full RGB output D2H completion. They include transfers, kernels and
intervening gaps, but omit CPU preparation before upload. Profiling changes
timing, so this narrower diagnostic must not replace the primary latency above.
Each cell is the median of 20 warm frames after 10 warm-ups. Each trace contains
30 complete full-output downloads. Actual transfer sizes and correlations are
preserved in the [trace summaries](evidence/transfer-windows.json).

| Input | Fused ms | Staged ms | Fastvideo ms |
|---|---:|---:|---:|
| hd444-r10 | 2.317 | 2.421 | 2.468 |
| hd420-r10 | 3.031 | 2.753 | 3.526 |
| 4k444-r10 | 6.354 | 7.544 | 6.645 |
| 4k420-r10 | 6.053 | 6.714 | 6.703 |

## What was ported

Enable `HUFFMAN_GPU_STAGED_DECODER=ON` in CMake. The existing fused GPU entropy
decoder is replaced, for eligible images, by three separate CUDA launches:

1. **Huffman parsing into tokens.** One CUDA thread parses each restart segment.
   It stores raw amplitude bits, amplitude category and zero-run advance, without
   signed-amplitude extension, coefficient scatter or DC prediction.
2. **RLE expansion.** A CUDA block cooperatively loads token planes for eight
   JPEG blocks into a padded shared-memory transpose. One warp then expands each
   8×8 block, prefix-scanning run advances and scattering signed values into
   GPUJPEG's natural-order coefficient blocks. The existing coefficient-buffer
   memset provides the omitted zeros.
3. **Segmented DC prefix scan.** One warp handles each restart segment/component.
   It scans DC deltas in coding order, selecting that component's blocks and
   adding a carry across 32-block tiles. This handles the multiple luma blocks
   per MCU in 4:2:2 and 4:2:0. Every restart segment begins with a zero predictor.

Existing dequantization/IDCT, postprocessing, and output download then run.
There is no additional intermediate host/device coefficient transfer.

The token is a 32-bit value: raw amplitude in bits 0–15, category in bits 16–19,
and run advance in bits 20–26. DC has advance zero, ZRL has advance 16, and EOB
is represented by the per-block token count. The address is
`tokens[token_ordinal * block_count + logical_block]`; the block list supplies
component identity, coding order, and destination addresses. A block reserves
64 tokens and one count byte, or **257 extra device bytes per block**. This is
about 25 MB for HD 4:4:4 and 100 MB for 4K 4:4:4. Capacity is retained per decoder,
grown when needed, and freed on destruction. Allocation failures return errors.

This is an independent adaptation to GPUJPEG, not a reconstruction of the
complete proprietary decoder. It retains GPUJPEG's expanded lookup tables,
serial stuffed-byte reader inside each restart thread, block-major IDCT layout,
and separate IDCT and color kernels. Fastvideo also uses parallel marker search
and byte unstuffing, small canonical Huffman tables, coefficient planes, and a
fused IDCT/color path. Those differences remain relevant to the comparison.

The first prototype used warp-per-block RLE with direct strided global loads.
A one-thread-per-block alternative improved read coalescing but made expansion
and scatter more expensive. The retained implementation cooperatively loads
the planes into a `[64][9]` shared tile, then uses warp expansion. Padding avoids
bank conflicts when a warp reads one block's token column. Masked loads avoid
reading unused token slots; every thread reaches the CTA barrier before an
invalid final warp can return. Signed-amplitude conversion is deferred out of
the serial Huffman loop. Earlier variants' profiles are retained as ablations,
not mixed into the final benchmark results. All tested variants regressed on the
initial HD 4:4:4 entropy-stage profile; this is not presented as a speed win.

## Eligibility and fallbacks

The option needs CUDA 9 or newer; older GPU architectures use the existing path.
The public decoder's eligibility rules are unchanged. It uses CPU entropy
decoding when there are fewer than 32 restart segments or unsupported GPU table
selectors. In particular, the no-restart cases in the benchmark exercise the
existing CPU fallback, **not** the new stages. Fastvideo's no-restart behavior
instead transcodes the entropy stream to restart partitions on the CPU before
GPU decoding, as established in the first investigation.

Grayscale, interleaved 4:4:4/4:2:2/4:2:0 and non-interleaved component scans were
tested. No progressive, arithmetic, higher-bit-depth or new JPEG-format support
is claimed. Defaults and the encoder are unchanged by this follow-up.

## Measurement boundaries

Every decoder consumes the same byte-identical input JPEG. The corpus contains
ten natural HD/4K files produced by the unmodified GPUJPEG encoder at quality 90,
with interleaved sampling and restart intervals 0, 1, 10 or 32. This avoids using
Fastvideo's trial encoder to create different or watermarked benchmark inputs.
Output is RGB8 for color and gray8 for grayscale, downloaded to CPU memory.

The reference GPUJPEG build includes the previous Huffman-table cache but leaves
staging OFF. The candidate differs by staging ON; both leave experimental encoder
serialization OFF. Thus the comparison isolates this decoder architecture from
the previous optimization. Fastvideo is the unchanged supplied 0.21.1.0 trial.
All use one application thread on the RTX 3050 8 GB, SM86, driver 617.42, CUDA 13.4,
Windows WDDM. GPU clocks were not locked. Results should not be generalized to
other GPUs or a broader corpus without measurement.

Three measurements are kept separate:

* **Complete memory-input-to-host-pixels latency.** GPUJPEG's `Decode Image:`
  begins before JPEG container parsing and ends after the synchronous RGB/gray
  download. For Fastvideo, a lightweight version-specific observer records its
  actual QPC counters, from header-load start through decode/download stop.
  This continuous interval covers memory
  parsing, entropy-tail copying, decoding, no-restart preprocessing when needed,
  and completed output download. The JPEG file is read once before repetition.
  These continuous intervals are the principal comparison. The separate header
  and codec intervals, plus the measured gap between them, are retained too.
* **Common profiled GPU pipeline window.** Nsight measures from the first
  payload-sized H2D copy through completion of the full raw output D2H copy.
  This includes transfers, kernels and gaps, but excludes CPU work before that
  upload. It is independent corroboration, not a full codec/API timer.
* **Whole CLI batch wall time per iteration.** Python measures child launch
  through exit and divides by the iteration count. This includes startup,
  warm-up, file I/O, console output and cleanup. It has a common outer boundary
  and is reported separately from steady-state codec work.

Fastvideo's printed JFIF-load number cannot simply be added to its decode and
download times: native instruction bytes show it adds a **cached, one-time file
read cost** to every displayed repetition. Such a sum can even exceed measured
whole-process time per iteration. This was detected by the process-wall sanity
check; the raw QPC observer avoids double-counting that cached charge. It changes
only the current spawned process's QPC import pointer, stores observations in
RAM and writes them after timing, without modifying the executable on disk or
subscribing to CUDA callbacks. The observer is restricted to this binary hash
and validates timer counts/frequency.

Fastvideo's printed decode-plus-download sum without header loading is retained
as a secondary, narrower boundary, **not** compared silently with GPUJPEG's
complete API interval. The native timer and one-time file-loader call chains were
checked through REA and corroborated with the synchronized D2H runtime trace; see
`methodology.md` for addresses, formulas and evidence IDs. **No Fastvideo
`-discard` flag was used**, because it bypasses output download in this binary.

The main experiment rotates codec order over five rounds, reversing image order
on alternate rounds. Each process decodes 110 times, with the first 10 samples
discarded for warm statistics. Tables use the median of the five run medians;
raw samples and round spreads are retained. Fastvideo's console prints two
decimal places per timer; directly observed QPC avoids that rounding. Profiling runs
are separate from the unprofiled benchmark. Binaries and inputs are hashed
before/after runs, and every final PNM output's shape and pixel count is checked.

## Correctness and limits

The independent harness compares both all quantized DCT coefficients, reordered
by the block list, and public decoded pixels. It includes dense quality-100
inputs, sparse/EOB inputs, optimized Huffman tables, odd dimensions, planar
scans, subsampling, 31/32/33-segment threshold cases and DC scans spanning
multiple 32-block tiles. Reuse and two nonblocking decoder streams are exercised
in the ordinary table configuration; constant-table tests use sequential calls
because that upstream option shares global constant memory.

A mixed GPU/CPU reinitialization test exposed a pre-existing CPU coefficient
buffer growth failure in unchanged GPUJPEG: GPU 4K decode → smaller no-restart
CPU decode → larger no-restart CPU decode. The baseline crashes before a staged
kernel is involved. That sequence is preserved as a separate reproducer and
excluded from reuse parity tests; all those images are still tested with fresh
instances. No unrelated upstream fix is hidden in this experimental port.

The full upstream shell/make regression suite and other GPU architectures were
not tested. The new path requires substantially more VRAM and additional kernel
launches. Keeping it optional is essential until results on the intended workload
justify enabling it.
## Output equivalence in the benchmark corpus

All ten final staged outputs are byte-identical to fused GPUJPEG. Fastvideo's
outputs have the expected dimensions/channel counts but are not assumed bit
identical across implementations. The cross-codec sample errors below compare
Fastvideo with fused GPUJPEG, not with an original lossless ground truth; color
upsampling/IDCT choices can change them. Per-file SHA-256 values and full error
statistics are in [pixel verification](evidence/pixel-comparison.json).

| Input | Fastvideo max sample error | Mean absolute sample error | PSNR dB |
|---|---:|---:|---:|
| hd444-r10 | 6 | 0.3818 | 52.23 |
| hd420-r10 | 3 | 0.0658 | 58.87 |
| hd422-r10 | 3 | 0.0653 | 58.94 |
| 4k444-r10 | 2 | 0.3870 | 52.17 |
| 4k420-r10 | 3 | 0.0546 | 59.60 |
| hdgray-r10 | 1 | 0.0168 | 65.87 |
| hd444-r1 | 6 | 0.3818 | 52.23 |
| hd444-r32 | 6 | 0.3818 | 52.23 |
| hd444-r0 | 6 | 0.3818 | 52.23 |
| 4k420-r0 | 3 | 0.0546 | 59.60 |


## Variability and CLI lifecycle cost

These are the minimum–maximum of the five warm run medians, not confidence
intervals. WDDM scheduling, unlocked clocks and ordinary host scheduling cause
variation. The corpus uses a small number of supplied scenes with multiple
sampling/restart configurations; it is not a broad image-distribution study.

| Input | Fused round range ms | Staged round range ms | Fastvideo round range ms |
|---|---:|---:|---:|
| hd444-r10 | 2.464–2.840 | 2.737–2.863 | 2.340–2.536 |
| hd420-r10 | 3.065–3.217 | 3.001–3.168 | 2.972–3.178 |
| hd422-r10 | 2.586–2.720 | 2.727–3.106 | 2.607–2.770 |
| 4k444-r10 | 7.244–7.588 | 8.644–9.347 | 6.849–7.076 |
| 4k420-r10 | 6.622–6.872 | 7.435–7.673 | 6.818–7.083 |
| hdgray-r10 | 1.344–1.495 | 1.442–1.695 | 1.267–1.467 |
| hd444-r1 | 3.184–3.398 | 3.405–3.613 | 2.000–2.124 |
| hd444-r32 | 3.347–4.125 | 3.470–3.785 | 3.480–3.820 |
| hd444-r0 | 18.575–20.573 | 18.581–19.668 | 26.733–32.861 |
| 4k420-r0 | 40.974–43.707 | 41.159–42.749 | 57.565–66.134 |

The following median process-wall/110 values include startup, initial file I/O,
all warm-ups, console output, final file write and cleanup. They have the same
outer boundary, but should not be read as steady-state decoder latency.

| Input | Fused ms/iteration | Staged ms/iteration | Fastvideo ms/iteration |
|---|---:|---:|---:|
| hd444-r10 | 5.343 | 5.669 | 6.482 |
| hd420-r10 | 5.876 | 5.869 | 7.183 |
| hd422-r10 | 5.534 | 5.768 | 6.788 |
| 4k444-r10 | 11.026 | 12.514 | 11.597 |
| 4k420-r10 | 10.302 | 11.231 | 11.785 |
| hdgray-r10 | 4.012 | 4.102 | 5.338 |
| hd444-r1 | 6.319 | 6.396 | 6.052 |
| hd444-r32 | 6.612 | 6.923 | 7.539 |
| hd444-r0 | 22.667 | 22.223 | 35.253 |
| 4k420-r0 | 45.806 | 46.502 | 69.879 |

## Deliverables and reproduction

- Complete source (`artifact: GPUJPEG-staged-source.zip`), with both experimental options OFF.
- Incremental decoder patch (`artifact: gpujpeg-staged-decoder.patch`), against the previous source archive.
- Cumulative patch (`artifact: gpujpeg-cumulative-upstream.patch`), against GPUJPEG commit `78e5eee3f75e0a2a2e6c1409000ac05d679b4af8`.
- [Build, correctness and benchmark instructions](reproduction/README.md).
- [Native timer proof and observation method](methodology.md).
- [Machine-readable latency summary](evidence/benchmark-summary.json), [full runs](evidence/benchmark-runs.json), raw timing logs (`artifact: evidence/benchmark-logs.zip`).
- [Pixel verification](evidence/pixel-comparison.json), [correctness](evidence/correctness-default.json), [constant-table correctness](evidence/correctness-const.json).
- [Provenance and SHA-256 manifest](manifest.json).

Both patches were clean-applied to their stated bases and the resulting 153
source files compared byte-for-byte after newline normalization. The source ZIP
is self-contained for the C/CUDA build; the optional Go submodule is omitted.
The original Fastvideo program, supplied images and proprietary GPU code are not
redistributed. No upstream PR was published.

The [original investigation](../fast-jpeg-investigation.md) records the broader
Fastvideo reverse engineering. This follow-up uses [GPUJPEG](https://github.com/CESNET/GPUJPEG)
at the commit above and [REA](https://github.com/morluto/rea) commit
`0ba4d6f43ad610dacc861a1548be1a5c4712cd61` (6.7.0), with Rizin 0.9.1.
