> Archived investigation document. See the [current branch guide](../../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# Reproduce the staged decoder experiment

The full source archive contains the prior encoder experiment and decoder table
cache, plus this follow-up's staged decoder. Both experimental CMake options
default OFF. The incremental `gpujpeg-staged-decoder.patch` applies to the
previous `GPUJPEG-source.zip`; `gpujpeg-cumulative-upstream.patch` applies to
upstream commit `78e5eee3f75e0a2a2e6c1409000ac05d679b4af8` and includes both
rounds of work. The optional Go submodule is not required or vendored.

## Build two matching decoder variants

Use a CUDA-capable x64 developer shell, CMake and Ninja. Architecture 86 matches
the tested RTX 3050; select your actual architecture on other hardware.

```
cmake -S GPUJPEG -B fused -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=86 -DBUILD_OPENGL=OFF -DBUILD_SHARED_LIBS=OFF -DHUFFMAN_GPU_WARP_SERIALIZATION=OFF -DHUFFMAN_GPU_STAGED_DECODER=OFF
cmake --build fused --parallel 4
cmake -S GPUJPEG -B staged -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=86 -DBUILD_OPENGL=OFF -DBUILD_SHARED_LIBS=OFF -DHUFFMAN_GPU_WARP_SERIALIZATION=OFF -DHUFFMAN_GPU_STAGED_DECODER=ON
cmake --build staged --parallel 4
```

The source requires CUDA 9+ to enable the staged option. Other CUDA/toolchain
versions and GPU architectures were not verified by this investigation.

## Correctness

`correctness/README.md` describes building `decoder_dump.cu` against each static
library and running `validate_decoder.py`. It compares every quantized DCT
coefficient and every public output byte, not just visual similarity. Pillow
with restart-marker support is needed only to generate the test JPEGs. Generated
fixtures and optional original natural images need not be distributed.

The two builds should have the same source revision/configuration apart from
staging. Build an additional `HUFFMAN_GPU_CONST_TABLES=ON` candidate for its
sequential test suite. The default-table suite additionally exercises concurrent
independent decoder instances; concurrent different tables in global constant
memory are not claimed safe.

For targeted sanitizer checks, use an input with at least 32 restart segments:

```
compute-sanitizer --tool memcheck --error-exitcode 99 --kernel-name kns=gpujpeg_huffman_staged_ staged/gpujpegtool.exe -d -n 2 input.jpg output.ppm
```

Repeat with racecheck and synccheck. A no-restart file takes the CPU entropy
fallback, so it does not test these kernels. The recorded runs targeted all
three staged kernels on different noisy/odd/optimized-table fixtures.

## Corpus

`corpus/manifest.json` records the exact natural image inputs used, their hashes,
sampling, quality, restart interval and output byte counts. The original image
samples come from the user-provided Fastvideo ZIP and are not redistributed.
`make_corpus.py` regenerates JPEGs using the fused GPUJPEG encoder, with explicit
sample-image directory, encoder, and destination arguments. Every decoder must
consume those same JPEGs; independently re-encoding each codec's input would
invalidate the comparison.

## Benchmark and timing

The measurement tools run codecs sequentially and retain full samples, variant
order, logs, binary/input hashes and output shapes. They do not set GPU clocks.
Run them with no competing GPU workloads. The final methodology and report
define the primary memory-input-to-host-output interval; kernel-only times are
diagnostics and cannot replace the transfer-inclusive result.

The Fastvideo executable is supplied by the user, with its original neighboring
runtime DLLs. It is never included in the source or evidence archives. Do not
use its `-discard` flag for this experiment: the flag bypasses the raw output
download. Use one CPU application thread, and return RGB8/gray8 to host RAM.

`profile_core.py` and `trace_summary.py` obtain a separate, identical GPU-pipeline
boundary from the start of the payload upload through completion of the full
raw output download. The SQLite summaries retain actual transfer byte counts
and correlations. That diagnostic excludes CPU preparation before the first
upload. Profiling and sanitizer runs are kept out of the unprofiled benchmark.

The Fastvideo native timing investigation and any lightweight timer observer
are documented in `../methodology.md`; exact executable identity and call sites
are mandatory for its version-specific observation. The observer is an analysis
tool for a newly spawned process, not a change to the original executable on
disk. Its timing overhead and verification are reported alongside the results.

Example commands from this `reproduction` directory (replace executable and
sample paths with your own). `make_corpus.py` writes a relative-path manifest
beside the generated JPEGs, using the exact supplied sample filenames.

```bat
python make_corpus.py --images FASTVIDEO_SAMPLE_DIRECTORY --encoder FUSED_GPUJPEGTOOL --output ../corpus
build_qpc.cmd
python benchmark.py --manifest ../corpus/manifest.json --baseline FUSED_GPUJPEGTOOL --staged STAGED_GPUJPEGTOOL --fastvideo FAST_JPEG_EXE --fastvideo-qpc-helper qpc_capture.dll --output benchmark --rounds 5 --iterations 110 --warmup 10
python profile_core.py --manifest ../corpus/manifest.json --baseline FUSED_GPUJPEGTOOL --staged STAGED_GPUJPEGTOOL --fastvideo FAST_JPEG_EXE --nsys NSYS_EXE --output profiles --iterations 30 --warmup 10
python check_pixels.py --manifest ../corpus/manifest.json --images benchmark/images --output pixel-comparison.json
```

The QPC helper is Windows-only and bound to the recorded Fastvideo SHA-256.
Set `CAPTURE_VCVARS` if Visual Studio's x64 environment script is installed
elsewhere. Its source and the exact records are supplied; rebuild the DLL
locally. `benchmark.py` rejects a different Fastvideo binary when the helper is
selected and validates every captured timer pair. Without the helper, its
Fastvideo primary fields are explicitly invalid: use the narrower printed
decode/download diagnostic or add validated timing support for that version.

The final primary is the continuous absolute-counter interval from header-start
to completed codec/download-stop. The separately measured header, codec and
intervening gap are retained as diagnostics. No cached disk-read charge is added.
The observer adds a small measured overhead to Fastvideo only; calibration was
about 0.039–0.046 microseconds per frame on this host, not subtracted from results.
The recorded warm gap medians were 0.2–0.3 microseconds.

The baseline's pre-existing mixed GPU/CPU coefficient-buffer resize crash is
documented in the correctness README with a minimal reproducer. It is not part
of this decoder optimization and is not silently fixed here.
