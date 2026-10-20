> Archived investigation document. See the [current branch guide](../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# GPUJPEG: parallel byte unstuffing and compact canonical Huffman tables

Both techniques are implemented and pass validation. The useful observed gain
is **parallel unstuffing with expanded Huffman tables on HD 4:2:0 R10**:
**2.935 ms versus 3.251 ms fused GPUJPEG**, a **9.7% lower median latency**;
Fastvideo measured **3.048 ms** on the same JPEG. This configuration beat fused
in four of five paired rounds; one round was 0.7% slower and the round ranges
overlap. It is a workload-specific result, not a general speed guarantee.

Compact canonical tables reduce the lookup representation from **532,480 to 1,536
bytes**, but did not produce a broad throughput improvement here. Both features
together measured **2.859 versus 2.592 ms fused** on HD444 R10 and **9.039 versus
7.477 ms fused** on 4K444 R10. Unstuff-only also regressed on those inputs.
Keep the new defaults **OFF**; the selectable configurations support further
testing on the intended workload.

Validation passed **394 exact coefficient-and-pixel comparisons** and six
strict sanitizer runs. The port retains the separate Huffman/RLE/DC stages;
Fastvideo's global marker discovery, coefficient planes and fused IDCT/color
pipeline remain different.

## Complete memory-input to host-pixel latency

Times include JPEG parsing, input preparation, H2D, decoding and completed D2H.
They exclude initial input and final output disk I/O. All four variants decode
the same JPEG bytes and return RGB8/gray8 to the CPU. Values are medians of five
run medians, each using 100 warm frames after ten discarded frames. The main run
has **200 sequential processes, 22,000 decodes and 20,000 retained warm samples**.
Positive percentages mean slower than fused GPUJPEG.

| Input Q90 | Fused ms | Both features ms | Unstuff only ms | Fastvideo ms | Both vs fused | Unstuff vs fused |
|---|---:|---:|---:|---:|---:|---:|
| hd444-r10 | 2.592 | 2.859 | 2.775 | 2.383 | +10.3% | +7.1% |
| hd420-r10 | 3.251 | 3.164 | 2.935 | 3.048 | -2.7% | -9.7% |
| hd422-r10 | 2.684 | 2.816 | 2.693 | 2.622 | +4.9% | +0.3% |
| 4k444-r10 | 7.477 | 9.039 | 8.999 | 6.942 | +20.9% | +20.4% |
| 4k420-r10 | 6.766 | 7.477 | 7.363 | 7.019 | +10.5% | +8.8% |
| hdgray-r10 | 1.538 | 1.539 | 1.518 | 1.414 | +0.1% | -1.3% |
| hd444-r1 | 3.237 | 3.517 | 3.504 | 2.116 | +8.6% | +8.2% |
| hd444-r32 | 3.345 | 3.604 | 3.251 | 3.498 | +7.7% | -2.8% |
| hd444-r0 (CPU fallback) | 19.382 | 19.511 | 19.374 | 30.368 | n/a | n/a |
| 4k420-r0 (CPU fallback) | 42.833 | 44.074 | 42.584 | 61.922 | n/a | n/a |

Both features are added to the experimental staged decoder. “Unstuff only” also
uses that decoder, retaining expanded Huffman tables. Fused refers to GPUJPEG's
original combined Huffman/RLE/DC kernel with the previously added table cache.
Neither new configuration replaces the no-restart CPU entropy fallback; its
timing differences are not GPU-stage acceleration.

## Feature ablation

Here C means compact tables and U means parallel unstuffing plus the word reader.
The four configurations come from the **same frozen source revision**, with
staging ON. The separate fused build is an external control. These are a distinct
three-round run on four inputs, with 60 frames/process and ten discarded frames:
60 processes and 3,000 retained warm samples. Do not compare these absolute
values with the main table as if they were simultaneous measurements.

| Input R10 | Fused ms | C0U0 ms | C1U0 ms | C0U1 ms | C1U1 ms |
|---|---:|---:|---:|---:|---:|
| hd444-r10 | 2.694 | 2.866 | 2.876 | 2.803 | 2.795 |
| hd420-r10 | 3.127 | 3.146 | 3.448 | 2.835 | 3.102 |
| 4k444-r10 | 7.322 | 8.720 | 8.660 | 8.820 | 8.877 |
| 4k420-r10 | 6.671 | 7.412 | 7.512 | 7.579 | 7.409 |

The unstuff-only configuration was promoted to the longer main run after the
HD420 ablation showed separated round ranges against both fused and combined.
This makes the selection process explicit. The main run is a subsequent check,
not proof that this configuration wins on every image. Per-round values, ranges
and paired ratios are retained in [ablation observations](evidence/ablation-compact.json).
The U flag measures compaction and the enabled word reader together; it does not
isolate their individual contributions.

## Independent transfer and kernel checks

These separate Nsight runs measure first compressed-payload H2D device activity
through full output D2H completion. Each cell is a median over 20 warm frames
after ten discarded frames. This narrower window omits CPU preparation before
upload, and profiling perturbs timing; it is a diagnostic, not a replacement
for the primary table.

| Input R10 | Fused ms | Both ms | Unstuff ms | Fastvideo ms |
|---|---:|---:|---:|---:|
| hd444-r10 | 2.213 | 2.444 | 2.414 | 2.396 |
| hd420-r10 | 2.995 | 2.898 | 2.544 | 3.126 |
| 4k444-r10 | 6.177 | 7.720 | 7.830 | 6.553 |
| 4k420-r10 | 5.929 | 6.714 | 6.712 | 6.590 |

Every one of the sixteen traces contains 30 full-output downloads. Every warm
combined frame has the unstuff kernel and token parser with compact/unstuffed
template booleans true/true; every warm unstuff-only frame has true unstuffing
with expanded-table parsing (false/true). Compact mode omits expanded-table
builds. [Trace summaries](evidence/transfer-windows.json) retain byte sizes,
correlations and launches; [kernel measurements](evidence/kernel-summary.json)
retain per-frame duration sums, launch medians and actual resource metadata.
These do not measure bandwidth, occupancy or isolate a single bottleneck.

The following stage times come from the same twenty warm frames. Each column is
a median of per-frame sums of matching launches. The entropy-total columns are
computed from each frame's actual sum, not by adding stage medians; minor sums
need not match due to median arithmetic. They exclude IDCT, color conversion,
transfers and host gaps, so they are explanatory diagnostics only.

| Input R10 | Fused entropy µs | Both parser µs | Both unstuff µs | Both RLE µs | Both DC µs | Both entropy total µs | Unstuff-only entropy total µs |
|---|---:|---:|---:|---:|---:|---:|---:|
| hd444-r10 | 489.5 | 532.4 | 19.2 | 197.6 | 36.0 | 785.4 | 734.5 |
| hd420-r10 | 1376.6 | 1193.2 | 21.3 | 104.4 | 22.0 | 1340.4 | 1011.9 |
| 4k444-r10 | 851.3 | 1462.4 | 47.2 | 730.6 | 132.3 | 2413.5 | 2372.2 |
| 4k420-r10 | 1096.0 | 1324.4 | 38.6 | 376.2 | 75.7 | 1815.8 | 1726.4 |

## What this experiment changes

This is an independent adaptation of the two techniques identified in the
supplied Fastvideo binary, extending the preceding three-stage decoder. It
keeps Huffman tokenization, RLE expansion and DC restoration separate, and keeps
GPUJPEG's existing block-major IDCT/color/output paths. It does not reconstruct
the complete proprietary backend.

The new compact tables store canonical length limits, minimum codes, symbol
starts and symbols. The four tables occupy **1,536 bytes**, rather than the
four 65,536-entry tables (524,288 bytes) and 10-bit quick table (8,192 bytes).
These figures compare lookup representations, not total decoder memory:
GPUJPEG's existing source-DHT buffers/uploads and natural-order data remain.
Selection uses a bounded lower-bound decision tree over code lengths rather
than indexing an expanded 16-bit table. Empty code lengths must preserve
nondecreasing limits; the test corpus explicitly exercises holes and actively
used 16-bit codes. Tables and their host staging/identity cache belong to each
decoder instance, including when images change DHT values under the same IDs.

Parallel unstuffing uses GPUJPEG's already parsed restart partitions. Threads
cooperatively remove the zero byte in each `FF 00` pair into disjoint padded
device slots, then the Huffman parser reads the unstuffed stream. No new
intermediate D2H copy is needed to obtain the retained byte count. This moves
the stuffed-byte decision out of the serial Huffman symbol loop.

Fastvideo's observed implementation instead uses a global markup pass,
count scans, byte compaction and GPU-generated marker endpoints. GPUJPEG still
finds/removes restart markers on the CPU; reusing those boundaries avoids
duplicating marker discovery in this port. Its no-restart CPU fallback remains
unchanged. No result on a no-restart file measures the new GPU stages.

The earlier token/count allocation remains **257 bytes per JPEG block**
(roughly 25 MB HD444 or 100 MB 4K444), in addition to the new unstuffed stream.
The port therefore remains a bandwidth/memory tradeoff, even though Huffman
tables become much smaller. Buffer lifetimes, padding, exact allocation and
configuration details are recorded in [implementation notes](implementation.md).

## Fair timing boundaries

Every codec receives the exact same ten JPEG files used in the preceding
experiment: quality 90, HD/4K, grayscale/444/422/420 and restart intervals
0/1/10/32. Color output is host RGB8; grayscale output is host gray8. These are
multiple encodings of a small number of supplied scenes, not a broad image
distribution study. The supplied Fastvideo trial executable is unchanged and
is not used to encode the inputs.

The reference is the original fused GPUJPEG decoder with the previously added
Huffman-table cache. The new candidate enables both techniques and the staged
decoder; the experimental encoder option is OFF in every decoder build.
All execute on the same RTX 3050 8 GB, SM86, Windows WDDM, driver 617.42 and
CUDA 13.4 host with one application thread. Clocks are not locked.

The primary metric is a **continuous memory-input through completed host-pixel
download interval**, excluding initial input and final output disk I/O:

- GPUJPEG: `Decode Image`, beginning before JPEG container parsing and ending
  after synchronous output download.
- Fastvideo: observed absolute QueryPerformanceCounter values from header-load
  start through codec/download stop, including the intervening host gap.

Fastvideo's printed “JFIF load” adds a cached one-time disk-read charge to every
repetition. Summing its printed load/decode/download values is invalid for
per-frame memory latency. The existing version-specific observer captures its
actual counters, changing only the spawned process's QPC import pointer. It
does not alter the executable file, enable CUDA callbacks, or write logs inside
the timed region. The recorded CPU-only calibration estimates 0.039–0.046
microseconds of observer overhead per frame on this host; it is not subtracted.
The exact binary hash, native proof and timing caveats are in
[timing methodology](methodology.md).

The main comparison uses five balanced rounds of 110 iterations, discarding
the first ten within each process. Codec order rotates and image order reverses
on alternate rounds. A headline cell is the median of five run medians.
Individual samples, per-round medians and their ranges are retained. Small
differences within run-to-run variation do not demonstrate a robust speedup.

A separate Nsight diagnostic measures first compressed-payload H2D activity
through full output D2H completion. It includes device work, transfers and
intervening gaps, but excludes CPU preparation before upload. Its narrower
boundary and profiling overhead mean it cannot replace the primary metric.
Whole CLI wall time divided by iterations is retained separately; it includes
startup, file I/O, all warm-ups, console output and cleanup.

## Limits

This opt-in experiment retains GPUJPEG's eligibility rules and JPEG format
support. No-restart and fewer-than-32-segment cases use CPU entropy decoding.
No new progressive, arithmetic or higher-bit-depth support is claimed. Only
the stated SM86 GPU/toolchain was tested; a complete upstream regression suite
and other GPU architectures were not exercised. Correctness fixtures are valid
JPEGs; sanitizer checks are not proof of general malformed-input hardening.

The pre-existing mixed GPU/CPU coefficient-buffer growth failure documented in
the preceding investigation is not changed by this work. Fresh instances cover
CPU fallback; repeated-instance tests use the documented valid GPU sequences.
The source retains previous optional experiments, but new defaults remain OFF.


## Correctness and validation

The combined candidate passed **140/140 default + 140/140 constant-table**
comparisons, all quantized coefficient bytes and all public output bytes exact.
Each configuration uses 38 inputs: fresh 38, repeated-instance sequence 34 and
two actual concurrent host-thread/nonblocking-stream instances 68. Thirty-four
inputs use GPU entropy; four exercise CPU fallback. Compact Huffman data are
private even with the constant-table option, so different-DHT concurrency was
tested in both configurations.

The other three feature combinations each passed 38 fresh comparisons, for
**394 exact comparisons total**. Ten synthetic stress JPEGs also match known
coefficients independently constructed by a CPU-only encoder, rather than relying
solely on the old decoder as an oracle. They include actively used 16-bit codes,
empty length ranges, dense 64-token blocks, sparse/EOB/ZRL, DC category 11,
41 restart segments with short final segments, and FF00 pairs crossing segment-
relative 16/32/64/128/256/512-byte boundaries. The original fixture suite retains
odd sizes, optimized DHTs, planar scans, subsampling, 31/32/33-segment routing and
DC scan carry cases.

Six strict Compute Sanitizer runs return exit 0 with zero errors/hazards:
memcheck/racecheck/synccheck on long-code valid data, memcheck on the large-hole
table, and two limited malformed-input memory-bounds diagnostics. The latter
are not valid-image parity tests or a general malformed-input guarantee. A
launch-dump run is retained separately as routing diagnostics; only the strict
no-dump runs are cited as clean sanitizer results. No source, library or harness
identity changed during validation.

The final benchmark outputs and cross-codec numerical comparison are recorded
in [pixel verification](evidence/pixel-comparison.json). Inputs, binaries and
the QPC observer have matching before/after hashes, and every GPUJPEG variant's
pixels match the fused output across all measured rounds.
Fastvideo is not bit-identical across implementations: the largest observed
sample difference is 6 out of 255; per-image mean errors, PSNR and hashes are
retained. This comparison is between decoders, not a lossless ground-truth test.

The standalone reproduction tools were also replayed after measurement:
ten natural corpus encodings, all 28 base fixtures and all ten stress fixtures
regenerated byte-identically. A further 38 fresh coefficient-and-pixel checks
passed after generating new baseline dumps, without reusing the prior reference
cache. These are separate reproduction checks, not added to the 394 headline
comparison count. [Replay evidence](evidence/portable-replay.json) records the
commands, identities and results.

## Round variation and complete CLI wall time

These ranges are minima–maxima of the five warm run medians, not confidence
intervals. WDDM scheduling, unlocked clocks and ordinary host scheduling vary.

| Input | Fused range ms | Both range ms | Unstuff range ms | Fastvideo range ms |
|---|---:|---:|---:|---:|
| hd444-r10 | 2.450–2.825 | 2.820–3.150 | 2.721–3.042 | 2.352–2.622 |
| hd420-r10 | 3.118–3.573 | 3.101–3.463 | 2.769–3.206 | 3.016–3.172 |
| hd422-r10 | 2.619–2.862 | 2.762–3.202 | 2.558–2.848 | 2.562–2.918 |
| 4k444-r10 | 7.334–7.760 | 8.963–9.624 | 8.864–9.860 | 6.864–7.166 |
| 4k420-r10 | 6.676–6.861 | 7.389–7.849 | 7.318–7.844 | 6.829–7.182 |
| hdgray-r10 | 1.349–1.836 | 1.430–1.621 | 1.409–1.710 | 1.256–1.671 |
| hd444-r1 | 3.186–3.706 | 3.475–3.985 | 3.482–3.850 | 2.040–2.234 |
| hd444-r32 | 3.339–3.594 | 3.524–4.153 | 3.191–3.473 | 3.455–3.903 |
| hd444-r0 | 19.137–19.855 | 19.329–19.537 | 19.081–20.471 | 29.575–31.630 |
| 4k420-r0 | 41.834–43.737 | 42.410–45.934 | 41.135–44.075 | 61.487–64.898 |

The following median process-wall/110 values include startup, disk I/O, all
warm-ups, logging, final output write and cleanup. Their outer boundary is
common, but they are not steady-state codec latency.

| Input | Fused ms/frame | Both ms/frame | Unstuff ms/frame | Fastvideo ms/frame |
|---|---:|---:|---:|---:|
| hd444-r10 | 5.714 | 6.237 | 5.852 | 6.819 |
| hd420-r10 | 6.232 | 6.214 | 5.817 | 7.578 |
| hd422-r10 | 5.738 | 5.766 | 6.001 | 6.912 |
| 4k444-r10 | 11.376 | 12.923 | 13.299 | 12.280 |
| 4k420-r10 | 10.332 | 11.249 | 11.143 | 12.476 |
| hdgray-r10 | 4.335 | 4.219 | 4.158 | 5.376 |
| hd444-r1 | 6.387 | 6.947 | 6.755 | 6.425 |
| hd444-r32 | 6.582 | 6.751 | 6.303 | 7.918 |
| hd444-r0 | 23.944 | 23.809 | 24.036 | 36.470 |
| 4k420-r0 | 49.937 | 50.790 | 49.054 | 70.169 |

## Deliverables and provenance

- Complete source (`artifact: GPUJPEG-compact-source.zip`), all new options OFF by default.
- Incremental patch (`artifact: gpujpeg-compact-decoder.patch`) against the preceding staged source archive.
- Cumulative upstream patch (`artifact: gpujpeg-cumulative-upstream.patch`) against GPUJPEG `78e5eee3f75e0a2a2e6c1409000ac05d679b4af8`.
- [Implementation details](implementation.md), [reproduction](reproduction/README.md), [timing methodology](methodology.md), [native timer proof](native-timer-proof.md).
- [Main numeric summary](evidence/main-compact.csv), [full runs](evidence/main-runs.json), raw logs (`artifact: evidence/main-logs.zip`).
- [Correctness and stress coverage](reproduction/correctness/README.md), [integrity](evidence/main-integrity-verification.json), [SHA-256 manifest](manifest.json).

Both patches clean-apply to their stated bases and reproduce all 155 source
files after newline normalization. Original Fastvideo executables, supplied
images and proprietary GPU code are not redistributed. No upstream PR was
published. The [preceding staged experiment](../staged-decoder/report.md) and
[original REA investigation](../fast-jpeg-investigation.md) retain earlier results;
they were not substituted for new measurements.
