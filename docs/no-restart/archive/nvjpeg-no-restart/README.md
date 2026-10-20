> Archived investigation document. See the [current branch guide](../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# Restart-free GPUJPEG encoding

Read report.md for the final measurements and conclusions, analysis.md for the nvJPEG reverse engineering evidence, and implementation.md for the new GPUJPEG data flow and limits.

## Build and use

Extract GPUJPEG-no-restart-source.zip. Its GPUJPEG folder contains the upstream C/CUDA source plus the restart-free encoder change. No nvJPEG library is linked into GPUJPEG. A CUDA toolkit with CUB is needed; the new option is enabled for CUDA11+ and SM3+ (validated here with CUDA13.4.59/SM86).

From an initialized Visual Studio x64 developer shell on this machine:

```powershell
cmake -S GPUJPEG -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=86 -DBUILD_OPENGL=OFF -DBUILD_SHARED_LIBS=OFF -DHUFFMAN_GPU_NO_RESTART=ON
cmake --build build --parallel 4
.\build\gpujpegtool.exe --encode --quality 90 --restart 0 --subsampled=4:2:0 input.ppm output.jpg
```

Set an architecture appropriate to another GPU when rebuilding. The supplied gpujpegtool.exe is the Windows x64 release built for SM86. Through the public API, set `gpujpeg_parameters.restart_interval = 0`; the normal encoder call selects the new route automatically. Positive restart intervals use the original encoder. `-DHUFFMAN_GPU_NO_RESTART=OFF` restores CPU entropy coding for RI0. Unsupported configurations use the documented CPU fallback.

The patch applies to upstream CESNET/GPUJPEG commit78e5eee3f75e0a2a2e6c1409000ac05d679b4af8. `source-verification.json` records independent application and comparison of every packaged source file. The source archive omits Git metadata and the uninitialized Go binding submodule; the standalone C/CUDA encoder build uses neither.

## Reproduce

The reproduction folder contains both public-API timing harnesses, fixture preparation, independent JPEG audits, full-byte comparison, encoder-state/concurrency checks and the benchmark scheduler. Keep original input images from your supplied package; they are not redistributed here. Preparation accepts their paths, records hashes, and creates the same packed raw inputs for all three encoders. Initialization, allocation and disk I/O are outside the warm timing window; raw upload, entropy/image processing, complete HOST JPEG retrieval and stream synchronization are inside.

Detailed commands are in reproduction/README.md. REA binary-analysis commands and profile capture instructions are in nvjpeg-reproduce.md. Full vendor binaries, extracted PTX/SASS and unfiltered REA strings are retained privately in the workspace rather than bundled with the new encoder. Focused Evidence IDs, original record hashes, tool commands and normalized findings accompany analysis.md.

Earlier work is indexed in ../README.md. Cleanup actions and exact file/archive hashes are documented in ../cleanup/README.md.
