> Archived investigation document. See the [current branch guide](../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

> Retained native-timer evidence from the preceding investigation. The supplied
> Fastvideo executable and observer hashes are unchanged. New measurements are
> in `evidence/main-summary.json`; old trace addresses below establish timer semantics.

# Fair decode measurement boundaries

Use the exact corpus JPEG SHA256 for every codec. Output is CPU RGB8 or gray8,
with the same dimensions/channels; Fastvideo's `-discard` is prohibited because
the default CLI branch uses it to bypass output download. Fastvideo benchmark runs
use `-info` and the single CPU-thread setting `-thread 1`.

## GPUJPEG application timer

In `src/gpujpeg_decoder.c`, `gpujpeg_decoder_decode` sets `coder->start_time`
before `gpujpeg_reader_read_image`. It calls synchronous `cudaMemcpy` D2H for
the default internal CPU output before `coder_process_stats`. The latter sets
`stop_time` using `gpujpeg_get_time` (`src/gpujpeg_common.c`). Therefore printed
`Decode Image:` includes JPEG container parsing, initialization/reconfiguration,
CPU/GPU decoder work, host-to-device transfer and completed device-to-host
output transfer. Input file reading and final output file writing are excluded.

Do not use `Decode Image GPU` or `Decode Image Bare` as transfer-inclusive
latency. They are separate diagnostic values.

## Fastvideo timer: native and runtime proof

For binary SHA256
`01735bf880587184119d70d56bda433ef01516e8d1139d880c765afca34dbac3`,
the default no-discard decode loop follows this call sequence (static preferred
PE image base `0x140000000`, offsets are not ASLR runtime addresses):

1. Header/SOS parsing and compressed entropy-tail copying finish first.
2. At `0x1400073d5`, call `0x14006bb80` records QPC start.
3. At `0x1400073f4`, call `0x14001efc0` (string `JPEG decoder. Decode`).
4. At `0x14000745b`, call output/download wrapper `0x14001cd70`.
5. That wrapper calls `0x140030b40` at `0x14001d219`.
6. Its implementation enqueues `cudaMemcpy2DAsync` through helper
   `0x140030260` (actual imported API at `0x1400302be`), **then calls
   `0x1400517d0` at `0x140030e9a` before returning**.
7. At `0x1400517d9`, this helper calls thunk `0x14006d80b`, which is
   `cudaStreamSynchronize_ptsz`. Thus the download has completed before return.
8. At `0x140007483`, record the ending download CUDA event through
   `0x140051530`; then `0x14000748b` calls `0x14006bad0` to read QPC stop.
9. Query the two CUDA transfer event durations through `0x140051350`, which
   calls `cudaEventSynchronize` and `cudaEventElapsedTime`.

The printed values are calculated as:

```
Decode = max(0, QPC_wall_ms - H2D_event_ms - D2H_event_ms) + H2D_event_ms
printed_D2H = D2H_event_ms
Decode + printed_D2H = max(QPC_wall_ms, H2D_event_ms + D2H_event_ms)
```

Consequently, the sum of the two per-iteration printed values includes
completed output download. The later CUDA-event query does not imply the QPC
stop precedes output completion: the earlier stream synchronization establishes
that completion.

Nsight Systems `work/fast-decode.sqlite`, final frame, corroborates this on one
thread (`globalTid=281692543649084`):

| Action | Start ns | End ns | Correlation |
|---|---:|---:|---:|
| `cudaMemcpy2DAsync` API |906315701|906330637|1620|
| 6,220,800-byte D2H device activity |906731209|907675948|1620|
| `cudaStreamSynchronize_ptsz` API |906331188|907700583|1621|
| ending `cudaEventRecord` API |907702231|907710355|1622|
| later `cudaEventSynchronize` API |907711584|907714102|1623|

The QPC interval includes host work inside SDK Decode, including the observed
no-restart entropy transcode, but excludes the preceding JPEG header/SOS load.
It also includes processing specific to the Fastvideo trial executable.

**The per-repeat JFIF load console label does not describe a disk reread.**
Outer dispatcher `0x1400099c0` calls file loader `0x14000ab70` at
`0x140009bd4`, before calling the repeated benchmark `0x140006f80` at
`0x14000a2d6`. The loader uses `seekg`/`tellg` for file size, allocates a host
buffer, calls `istream::read` at `0x14000ad6e` and closes the file at
`0x14000adbd`. The repeated loop starts at `0x140007200`; it reads the already
loaded pointer/length at list-node offsets+0x58/+0x60, constructs a memory
stream, parses JPEG headers/SOS, and copies the original compressed tail using
`memcpy` at `0x1400072bd`. The input-load timer starts at `0x14000720c` and
reads elapsed QPC at `0x1400072e9`. None of these repeat-loop calls opens or
reads a disk file.

There is a second, critical distinction: the printed JFIF-load time adds a
cached initial disk-load charge to the actual per-repeat memory-parse interval.
At `0x1400072fc`, the original bytes `f3 44 0f 58 43 6c` encode
`addss xmm8, dword ptr [rbx+0x6c]`, immediately after converting elapsed
header-load QPC to milliseconds. The initial loader stores its elapsed time
in its file-object field+0x5c at `0x14000ae60`; the list-node layout adds0x10.
This cached initial cost is added again to **every printed repetition**, even
though the disk read only occurred once. Consequently, summing printed
JFIF-load + Decode + D2H is **invalid as per-frame memory latency**. In the
first diagnostic run, it could even exceed amortized whole-process wall time.
Those first150 uninstrumented runs are retained as diagnostics; their summed
primary fields are invalid and must not be used for performance conclusions.

## Direct observation of the two host QPC intervals

The final150 balanced runs use an independently written, version-bound helper
`qpc_capture.cpp`. NVIDIA's documented `CUDA_INJECTION64_PATH` mechanism loads
the helper into only the explicitly spawned Fastvideo child. It does not
subscribe to CUPTI, enable activity tracing, or invoke any CUDA APIs. Instead,
it replaces **only that executable's imported QueryPerformanceCounter pointer**
with an observer forwarding every call to the original Windows function.
The original executable file and JPEG bytes are not modified.

The observer recognizes tail-call return RVAs0x7211 (header-load timer start)
and0x73da (codec timer start), and the elapsed-QPC helper return RVA0x6bae7.
At each corresponding stop it retains the original counter values and timer
frequency from the existing timer object, entirely in RAM. The wrapper's
frequency/start/stop fields were independently checked against disassembly.
The file is written only during process detach, after timed decoding finishes.
The benchmark runner enforces the exact investigated Fastvideo SHA256 and
requires exactly110 alternating load/decode pairs, one thread, positive
monotonic intervals, and one frequency equal to Windows
`QueryPerformanceFrequency` (10,000,000Hz on this host).

The retained absolute counters also preserve the intervening host gap between
header parsing and codec invocation. Postprocessing validates
`load.start <= load.stop <= decode.start <= decode.stop` for each pair and
uses a **single continuous interval** as the final primary metric:

```
Fastvideo primary_ms = 1000 * (decode.stop - header_load.start) / QPC_frequency
GPUJPEG primary = printed Decode Image (continuous host API interval)
```

This includes repeated memory header/SOS parsing, compressed-tail copy, CPU
entropy preparation, host-to-device upload and completed device-to-host output
download. It excludes the cached initial disk-read charge for Fastvideo and
input/output disk I/O for both. Unlike a two-interval sum, this continuous
measurement includes the host gap, which contains timer-return arithmetic,
dimension checks and output-format selection in warmed-up iterations. First-
frame information printing also occurs there but is discarded with warm-up.
The final warm gap medians are0.2–0.3 microseconds across the corpus. The
two-interval sum and explicit gap remain separate diagnostic fields. QPC
observations also avoid the Fastvideo console's two-decimal rounding.

`finalize_qpc.py` computes the continuous interval from the immutable original
TSV counters. It retains the previous sum-based processed JSON for traceability
and verifies that no raw QPC TSV file changed. The final primary table is
`final-qpc-benchmark/summary.json`, field `primary_median_round_ms`.

No stack unwinding or timed file writing is used. The start path adds a few
branches/pointer assignments; the stop records are written after the sampled
stop counter has already been read. A separate CPU-only microbenchmark runs
seven rounds of200,000 calls and deliberately records on every observed call
(versus only two of four actual per-frame calls). It estimates a conservative
four-call overhead of0.039–0.046 microseconds per frame on this host. This is a
calibration estimate, not a formal upper bound under arbitrary scheduling.
Full calibration results are retained in `qpc-overhead.txt`.

To recompute the final continuous interval after moving the result folder:

```
python finalize_qpc.py PATH_TO_RESULT_DIRECTORY
```

That directory should contain `plan.json`, `runs.json`, `summary.json` and its
`logs/` directory with the immutable QPC TSV files. The tool accepts the recorded
absolute paths while they exist, otherwise it resolves each log basename in
the moved result directory's `logs/`. No GPU or codec execution is involved.

## Common independent corroboration

`trace_summary.py` reads an Nsight Systems SQLite export for either codec. It
counts a full image download only when its D2H activity has the expected raw
output byte count. For each image it measures from the first payload-sized H2D device activity
after the preceding full output download through the current output download's
completion. This window includes input/output transfers, kernels and intervening
gaps. It excludes CPU work before first upload, so it is a transfer-inclusive
GPU pipeline diagnostic, not full codec latency. Discard initial iterations in
both codecs. Preserve the trace and exact full-output-copy count.

`benchmark.py` additionally measures Python `perf_counter_ns` from launching
each child to its exit, divided by the total iteration count. This is the same
outer boundary for all codecs, including initialization, input/output file I/O,
console logging, warm-up iterations and cleanup. It is reported separately
because amortized CLI lifecycle cost is not steady-state memory-to-memory API
latency. Alternating rounds rotate codec order and reverse image order to
reduce monotonic thermal/clock bias. No clocks are locked; five rounds are
retained individually.

For the four core q90 cases, `profile_core.py` parses the sequential JPEG header
and counts entropy data bytes after SOS, excluding RST and EOI markers while
keeping byte stuffing. This exact size is used as the minimum H2D payload size:
GPUJPEG removes restart markers before uploading, while Fastvideo uploads them.
This excludes smaller table/segment uploads for the tested core files. Every
selected payload's actual size/correlation is retained for review. Initial
setup uploads may be larger, but the first10 frames are discarded. This is a
declared selector for the one-scan test corpus, not a general JPEG decoder.
All actual transfer sizes remain in the original Nsight trace.

Fastvideo prints each per-frame timing to two decimal places; adding the two
rounded values gives about0.01ms quantization uncertainty per frame. GPUJPEG
prints four decimals. Do not infer a meaningful win from differences comparable
to this reporting precision, the spread between rounds, or profiling overhead.

## Provenance of native timer conclusions

REA6.7.0 executed Rizin0.9.1 through `execute-rizin-command`; source bytes were
unchanged. Retained REA evidence files in `evidence/rea-timer-evidence.zip` contain complete
commands, binary hashes, stdout/stderr and Evidence IDs. The `rea-fast-*` files
cover timer xrefs, QPC helpers, download dispatch, memcpy helper and synchronous
download completion. Rizin occasionally emits invalid instructions on unrelated
linear paths; the specific calls above are corroborated by control-flow
disassembly and the real CUDA runtime timeline. No whole-function source
reconstruction is claimed.


### Exact REA Evidence IDs

| File | Evidence ID |
|---|---|
| rea-fast-download-impl.json | ev_2da8b3ba231fe305330698ac982e1c8870355093b0e117a1f0556122eb59cd3d |
| rea-fast-download-wrapper.json | ev_dcfb3c75e5790471711082360026bfecc6b49a0af339241a5f0f6db47df278fb |
| rea-fast-file-constructors.json | ev_6b297e1cf80ac50587e6d8db226d2c207bb110d03fa08a26956c4d2d211b044e |
| rea-fast-file-read-once.json | ev_3a45763b45c37c8d9fa32f08ba4f30d8d6be8a590c06af79d77360aac103cd2a |
| rea-fast-load-function.json | ev_d06cefd6c387572c2a6ab0bdc2928ade9a1076422bfba23d99775c752efcfbf7 |
| rea-fast-load-timer-bytes.json | ev_eb1d7cedb894d5c00e6a82c4b97f194a31b5dcfd290a918d5709a9fe192a502d |
| rea-fast-outer-dispatch.json | ev_4d3b70c0227a0b710fd692649c27837af6a8e7131b6aba2c811a0b35b07354f0 |
| rea-fast-outer-load.json | ev_bb62fca0a5db2fb2b37d9e28c65ba12de83bfbeb4c42eeb2f7d5b2e4029a93c5 |
| rea-fast-sos-load.json | ev_fcc539a5f66953715b1bb431a7637ec9d574c0a7b00ce3f23f6dbe4e2229b493 |
| rea-fast-sync-wrapper.json | ev_a08de3187c52b153c38246c61038a61954f8b1777989704a4bc835042fbde91c |
| rea-fast-timer-disasm.json | ev_e40ad4036760bdcbcd7efce703b934bc24e57cf75bdcd9cfb8a362c1ea150c7b |
| rea-fast-timer-functions.json | ev_2a2f074ae9e61e053d966af0dd73ec11699b43dbf8871b77c0ad39b5587f4772 |
| rea-fast-timer-verify.json | ev_6ce1d0dd405cf1576291e93c1bc1ea1359e11233cbfe3829f1d534cbcbcf2d97 |
| rea-fast-timer-xrefs.json | ev_4dc46185bdfc15d1de09493b204fcdb87129916da038ab7ce5ad2d372012c9b6 |
