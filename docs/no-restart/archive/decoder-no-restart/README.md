> Archived investigation document. See the [current branch guide](../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# Restart-free GPUJPEG decoder prototype

The encoder is locked at tag `encoder-r0-locked-20261020`; its recoverable Git bundle and hashes are in [encoder-locked](../encoder-locked/LOCK.json). This package adds an independently implemented experimental GPU decoder. Read [results](report.md) and [implementation](implementation.md).

Unzip source (`artifact: GPUJPEG-decoder-source.zip`). With a CUDA toolkit and compatible C++ compiler:

```sh
cmake -S GPUJPEG -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=86 -DBUILD_OPENGL=OFF -DBUILD_SHARED_LIBS=OFF -DHUFFMAN_GPU_NO_RESTART=ON -DHUFFMAN_GPU_NO_RESTART_DECODER=ON
cmake --build build --config Release
```

Use a clean build directory. The new decoder option defaults **OFF** in source. The supplied Windows SM86 executable was compiled **ON**; it requires a compatible NVIDIA driver/GPU. For other GPUs set the corresponding architecture and rebuild. The new route is selected automatically for eligible restart-free JPEGs; normal GPUJPEG CLI decoding applies (`gpujpegtool --help`). No new public API is needed.

Incremental patch (`artifact: decoder-no-restart.patch`) applies to the locked encoder commit, not upstream main. Git bundle (`artifact: decoder-development.bundle`) retains both the locked encoder tag and the decoder branch. Complete encoder-only bundle (`artifact: encoder-locked-complete.bundle`) is also supplied. The original read-only lock bundle came from a shallow checkout and failed a fresh-clone test despite passing `git bundle verify`; it remains unchanged as a historical artifact. The two new bundles include the fetched ancestor history and are tested by fresh clones. Verify `manifest.json` hashes before using downloaded artifacts.

```sh
git clone decoder-development.bundle GPUJPEG-history
git -C GPUJPEG-history checkout decoder-r0-development
```

The bundle contains no remote HEAD, so cloning may print a checkout warning; the explicit checkout selects the requested branch. For encoder-only recovery clone `encoder-locked-complete.bundle` and explicitly check out `encoder-r0-locked-20261020` instead.

## Reproduce the timings

Unzip `fixtures.zip`. On the test machine, the supplied static harnesses can be used directly:

```sh
python reproduce/reproduce.py --locked reproduce/bench-locked.exe --candidate reproduce/bench-candidate.exe --fixtures fixtures --out rerun
```

The output directory must be new. This runs 5 randomized rounds, 110 calls per process, and discards the first 10 calls. Each call includes parsing, compressed H2D transfer, GPU work and full host pixel readback. Disk IO and decoder creation are outside the timer. It also checks output hashes across both implementations. Fixture names `flat-*-0` use standard tables; `flat-*-1` use optimized tables.

To rebuild the harnesses on Windows, run inside a Visual Studio x64 tools prompt with nvcc on PATH. Build the candidate library as above, and a separate library with `-DHUFFMAN_GPU_NO_RESTART_DECODER=OFF` for the locked decoder. Compile `decode_bench.cu` against each respective source/build include directory and library:

```sh
nvcc -O2 -std=c++17 -Xcompiler /MD -Xcompiler /Zc:__cplusplus -DGPUJPEG_STATIC -IGPUJPEG -Ibuild reproduce/decode_bench.cu build/gpujpeg.lib -o bench-candidate.exe
```

Adjust library paths for multi-configuration builds. Repeat with the OFF build for `bench-locked.exe`. Equivalent source builds are preferable to distributing architecture-specific executables. The measured baseline actually linked the pre-decoder locked library; its file hashes are recorded in the configuration.

## Reproduce coefficient and pixel checks

Build `reproduce/decoder_dump.cu` with the same nvcc command and corresponding library. Its CLI is `dump OUTPUT_DIRECTORY single|dual JPEG [JPEG ...]`; create the output directory first. `single` reuses one decoder and decodes the supplied list twice. `dual` uses independent host threads, decoders and CUDA streams. The harness writes final quantized coefficients in block-list order, pixels and metadata. Compare SHA-256 of both binary outputs from original and candidate builds. Batch at most 8 inputs to limit temporary disk use. The packaged `dump-original.exe` links unmodified upstream GPUJPEG; `dump-candidate.exe` links this prototype.

The measured validation/benchmark/interop drivers are supplied under `reproduce/measured-*`; their paths describe this workspace and must be adjusted to rerun elsewhere. `jpeg_checks.py` independently audits JPEG markers and entropy framing. All 102 fixtures were audited as RI0 with no restart markers. The two `known-zero-*` fixtures have independently known all-zero coefficients.

The raw evidence archive includes test stdout/stderr, metadata, build commands and Nsight captures. Its internal manifest hashes every member. Profiles are diagnostic and were collected separately from benchmark timings. `evidence/` contains searchable summary JSON, raw timing samples and sanitizer logs. Existing source licensing is in `GPUJPEG/COPYING`; no jpeggpu source or proprietary decoder binary is included.
