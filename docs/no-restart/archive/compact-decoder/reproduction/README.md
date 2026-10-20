> Archived investigation document. See the [current branch guide](../../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# Reproduce compact Huffman tables and parallel unstuffing

Extract `GPUJPEG-compact-source.zip`. The incremental
`gpujpeg-compact-decoder.patch` applies to the preceding
`staged-decoder/GPUJPEG-staged-source.zip`; the cumulative patch applies to
GPUJPEG commit `78e5eee3f75e0a2a2e6c1409000ac05d679b4af8` and includes all
three rounds of work. Both patches were clean-applied and their source trees
compared with the archive. The optional Go submodule is not needed for C/CUDA.

## Build

Use a CUDA x64 developer shell with CMake/Ninja. The tested architecture is
SM86 (RTX 3050); no other GPU architecture/toolchain was verified.

```bat
cmake -S GPUJPEG -B fused -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=86 -DBUILD_OPENGL=OFF -DBUILD_SHARED_LIBS=OFF -DHUFFMAN_GPU_WARP_SERIALIZATION=OFF -DHUFFMAN_GPU_STAGED_DECODER=OFF -DHUFFMAN_GPU_COMPACT_TABLES=OFF -DHUFFMAN_GPU_PARALLEL_UNSTUFF=OFF
cmake --build fused --parallel 4
cmake -S GPUJPEG -B combined -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=86 -DBUILD_OPENGL=OFF -DBUILD_SHARED_LIBS=OFF -DHUFFMAN_GPU_WARP_SERIALIZATION=OFF -DHUFFMAN_GPU_STAGED_DECODER=ON -DHUFFMAN_GPU_COMPACT_TABLES=ON -DHUFFMAN_GPU_PARALLEL_UNSTUFF=ON
cmake --build combined --parallel 4
```

The new options both require staging ON and CUDA 9+. GPUs below SM3 are
explicitly rejected with these options. They default OFF, as does staging.
For the feature ablation, build all four combinations of COMPACT_TABLES and
PARALLEL_UNSTUFF with staging ON from this same source revision. The unstuff
option includes its prepass and the marker-free word reader. Do not substitute
the older staged executable as the both-OFF control: wrapper/refactoring can
affect generated code independently of the two flags.

## Inputs and correctness

The original user-supplied images and Fastvideo executable are not distributed.
The exact source/input SHA-256 identities appear in `../corpus/manifest.json`.
Generate all codec inputs once, using GPUJPEG; do not re-encode separately for
each codec or use the Fastvideo trial encoder.

```bat
python make_corpus.py --images FASTVIDEO_SAMPLE_DIRECTORY --encoder FUSED_GPUJPEGTOOL --output ../corpus
```

The independent correctness harness compares every quantized coefficient and
public output byte. It tests the original 28 inputs and separately generated
known-coefficient stress images. `correctness/README.md` records exact commands,
counts, GPU/CPU coverage, table-memory configurations and concurrency scope.
Sanitizer logs are in `../evidence`. No-restart images exercise CPU entropy
fallback, not compact/unstuff kernels; choose at least 32 restart segments for
GPU-path tests.

## Transfer-inclusive comparison

Use no competing GPU workloads. Measurements are sequential; the runner rotates
codec order and reverses image order between rounds. It hashes inputs, binaries
and the timer observer before/after, checks returned CPU pixel sizes, and keeps
all console/QPC samples. The `staged` label in the main run denotes the new
**combined** candidate; exact flags and binary identities are recorded.

The Windows QPC observer is restricted to the recorded supplied Fastvideo hash.
It captures a continuous header-start through completed-download-stop interval,
excluding the cached initial disk-read charge in Fastvideo's printed load time.
It changes only the spawned process's QPC import pointer and does not modify the
executable on disk. `../methodology.md` documents native proof and overhead.
Build the supplied source locally; set `CAPTURE_VCVARS` if the x64 Visual Studio
environment script is installed elsewhere. No observer binary is redistributed.

```bat
build_qpc.cmd
python benchmark.py --manifest ../corpus/manifest.json --baseline FUSED_GPUJPEGTOOL --staged COMBINED_GPUJPEGTOOL --candidate-label staged --extra unstuff_only=UNSTUFF_GPUJPEGTOOL --fastvideo FAST_JPEG_EXE --fastvideo-qpc-helper qpc_capture.dll --output benchmark --rounds 5 --iterations 110 --warmup 10
python profile_core.py --manifest ../corpus/manifest.json --baseline FUSED_GPUJPEGTOOL --staged COMBINED_GPUJPEGTOOL --candidate-label staged --extra unstuff_only=UNSTUFF_GPUJPEGTOOL --fastvideo FAST_JPEG_EXE --nsys NSYS_EXE --output profiles --iterations 30 --warmup 10
python check_pixels.py --manifest ../corpus/manifest.json --images benchmark/images --output pixel-comparison.json --extra-gpujpeg unstuff_only
```

Run `python benchmark.py --help` for additional arbitrary-variant ablation
options. The four ablation builds must differ only in feature flags. Nsight
profiling follows the ordinary latency runs and confirms kernel routing and
full output D2H counts. Its device transfer window omits CPU work before upload
and must not replace the full primary latency. Whole-process wall/iteration
includes initialization, all warm-ups, logging and disk I/O; report separately.

The corpus tools support a relative-path manifest. Raw run metadata retains
original absolute execution paths as provenance; the packaged timing
postprocessor can locate moved raw logs by basename under its result directory.
