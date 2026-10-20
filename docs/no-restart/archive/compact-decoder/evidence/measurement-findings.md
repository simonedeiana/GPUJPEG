> Archived investigation document. See the [current branch guide](../../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# Measurement findings: compact tables and parallel unstuffing

All measurements use identical JPEG hashes and completed CPU RGB8/gray8 output.
The primary interval is continuous memory-input parsing through completed output
download, using GPUJPEG's host API timer and the version-bound Fastvideo QPC
observer. See `methodology.md` and `native-timer-proof.md` for exact boundaries.

## Main measurements

Five rotating rounds, 110 frames/process, first ten discarded; 200 processes,
20,000 warm frames. Values below are the median of five round medians, in ms.
`combined` is result label `staged`: compact canonical tables plus parallel
unstuffing, layered on the staged Huffman/RLE/DC decoder. `unstuff` keeps the
lookup table while enabling the parallel prepass.

| Exact input | Fused | Combined | Unstuff only | Fastvideo |
|---|---:|---:|---:|---:|
| HD444 R10 |2.59240|2.85860|2.77540|2.38350|
| HD420 R10 |3.25080|3.16385|2.93520|3.04790|
| HD422 R10 |2.68390|2.81585|2.69280|2.62210|
| 4K444 R10 |7.47695|9.03910|8.99945|6.94185|
| 4K420 R10 |6.76625|7.47720|7.36345|7.01870|
| HD gray R10 |1.53755|1.53875|1.51770|1.41450|
| HD444 R1 |3.23725|3.51680|3.50360|2.11570|
| HD444 R32 |3.34540|3.60410|3.25140|3.49820|
| HD444 R0 |19.38230|19.51060|19.37380|30.36800|
| 4K420 R0 |42.83270|44.07365|42.58425|61.92190|

The hybrid unstuff-only configuration gives a 9.7% lower median than fused on
HD420 R10 and 3.7% below Fastvideo. It is not a general speedup. In that main
case, paired hybrid/fused round ratios include one slight loss; the independently
preceding ablation was more consistent. Keep all individual round medians and
ranges from `compact.json` when interpreting differences. No-restart GPUJPEG
still uses the existing CPU fallback; its small inter-build differences cannot
be attributed to GPU compact or unstuff kernels.

All 200 runs succeeded. Every GPUJPEG variant produced exactly the same pixel
bytes as fused on every input; each variant was deterministic across rounds.
All ten input files, four executable files and observer DLL matched their
before/after hashes. The 250 raw observations comprise 200 stdout logs plus 50
Fastvideo QPC TSV files. See `main-benchmark/integrity-verification.json` and
`raw-observation-sha256.json` for provenance.

## Same-revision 2x2 ablation

Three rounds, 60 frames with ten discarded, four core images and five variants
produced 60 successful processes. The four experimental builds come from one
frozen revision and differ only in compact/unstuff CMake options. Fused remains
the independent control. The historical prior-stage binary is not used to
attribute feature changes.

For HD420 R10, unstuff-only round medians were
2.82665/2.83510/2.87880ms, compared with combined
3.10200/3.06775/3.11525ms and fused 3.12690/3.14395/3.07230ms. These separated
ranges motivated the fourth main variant. Other core images did not show a
consistent speed benefit. `ablation/compact.json` retains all round medians,
range endpoints and paired per-round ratios for all 20 case/configuration rows.

## Trace proof and explanatory measurements

Sixteen independent Nsight traces contain 480 expected-size output D2H copies,
30 per trace, with ten warmups removed. All 80 warm combined frames execute
`gpujpeg_huffman_parallel_unstuff_kernel` and token-parser template flags
`COMPACT=1, UNSTUFFED=1`. All 80 warm unstuff-only frames execute the prepass and
`COMPACT=0, UNSTUFFED=1`. Hashes matched before and after profiling. These checks
establish executed code paths, independently of compile-option labels.

Representative HD420 warm kernel medians in microseconds:

| Operation | Fused | Combined | Unstuff only |
|---|---:|---:|---:|
| Fused entropy decoder |1376.644|—|—|
| Parallel unstuff |—|21.296|21.440|
| Token parser |—|1193.220|864.371|
| RLE expansion |—|104.385|104.529|
| DC restoration |—|21.968|21.728|

The compact parser uses 31 registers/thread and the hybrid parser 26; both
launch 128 threads/block with zero shared-memory bytes. The prepass uses 256
threads/block, 22 registers/thread, zero shared memory. This is actual Nsight
launch metadata, not an occupancy measurement. Each table entry is the median
of that kernel's summed launch durations in each warm frame; adding medians is
not the median of the total pipeline.

HD444 illustrates the staging cost: combined token parsing is 532.353us and
hybrid 481.474us, but RLE~197.6us, DC~36.1us and unstuff~19.1us are additional,
compared with 489.538us for fused entropy. At 4K444 the parser alone is about
1.45ms, then adds RLE~0.73ms, DC~0.13ms and unstuff~0.047ms, compared with
0.851ms fused entropy. Compact storage alone does not establish faster parsing,
and the staged data movement remains material.

`profiles/kernel-summary.json` contains warm per-frame kernel medians and all
observed launch resources. `transfer-windows.json` retains every launch duration
inside each payload-H2D-to-output-D2H window. Those profiled windows exclude CPU
pre-upload work and are secondary diagnostics; the main table uses unprofiled
application host latency. Raw reports and SQLite files are indexed in
`profiles/report-sha256.json` for independent inspection.
