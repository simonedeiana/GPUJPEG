> Archived investigation document. See the [current branch guide](../../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# Reproducing the RI0 comparison

Use a CUDA-capable GPU, a CUDA Toolkit with nvJPEG, CMake, a compatible C/C++ compiler, Python 3.9+ and Pillow. The measured machine used CUDA 13.4.59, nvJPEG DLL 6.14.11.1322, MSVC 14.51, RTX 3050 SM86, driver 617.42, Xeon E5-1650v4 and Windows 10 build 19045. These numbers describe this machine; they are not promised results on other systems.

Build the original GPUJPEG revision `78e5eee3f75e0a2a2e6c1409000ac05d679b4af8` in a separate source/build directory. Build the candidate source from this deliverable in another directory. Both measured builds used Release, static GPUJPEG, OpenGL disabled and SM86. The candidate enabled `HUFFMAN_GPU_NO_RESTART`; the original checkout does not have that option. `HUFFMAN_GPU_CONST_TABLES=OFF` was unchanged and applies to decoding. On Windows, start in the Visual Studio x64 developer environment; use `/MD` consistently when linking the static GPUJPEG library into the CUDA harness.

```powershell
cmake -S ORIGINAL_SOURCE -B ORIGINAL_BUILD -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=86 -DBUILD_OPENGL=OFF -DBUILD_SHARED_LIBS=OFF
cmake --build ORIGINAL_BUILD --parallel 4
cmake -S CANDIDATE_SOURCE -B CANDIDATE_BUILD -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=86 -DBUILD_OPENGL=OFF -DBUILD_SHARED_LIBS=OFF -DHUFFMAN_GPU_NO_RESTART=ON
cmake --build CANDIDATE_BUILD --parallel 4
```

Replace `86` with the target GPU architecture. CUDA 11+ and SM3+ are required for the new path; unsupported cases use the existing CPU fallback. An additional candidate build with `-DHUFFMAN_GPU_NO_RESTART=OFF` verified that fallback compiles and produces the original JPEG bytes.

`build-gpujpeg-harness.cmd` and `build-state-check.cmd` show the measured Windows commands and exact compiler settings. Adjust the workspace-relative source/build paths and the VS installation path. Each benchmark executable must link its own corresponding GPUJPEG library. Do not link both versions into one executable. The public benchmark harness CLI is:

```text
gpujpeg_encode_bench --input RAW --width W --height H --channels 1|3 --sampling gray|444|422|420 --quality 90 --interleaved 1 --optimized 0 --pinned 0 --iterations 110 --warmup 10 --output OUT.jpg
```

The companion `nvjpeg_encode.cpp` uses an explicit GPU encoder backend, baseline DCT, RI0 and ordinary Huffman tables. Compile it with optimization and link the Toolkit's `nvjpeg` and `cudart` libraries. Its corresponding CLI has the same fields except `--interleaved`; all measured nvJPEG color images are interleaved. On Windows, put the Toolkit `bin/x64` and `bin` directories on PATH. The Python runners do this from `CUDA_PATH` when set. Posix builds can compile the public CUDA harness with `nvcc -O2 -std=c++17 -DGPUJPEG_STATIC`, the GPUJPEG source/build include directories and its static library; replace the Windows CRT flags with the platform defaults.

Generate raw inputs from the supplied natural PNM images. The scripts need Python/Pillow, not GPU access for this step:

```powershell
python prepare_inputs.py --hd-rgb PATH_TO_wild.8.ppm --large-rgb PATH_TO_wild.4k.8.ppm --gray PATH_TO_wild.8.pgm --output inputs
```

If Pillow is in a nonstandard folder, add `--pillow-path FOLDER` to this and the quality runners. The manifest records SHA256 for every exact packed raw input and source PNM. HD color/gray are 1920×1080; 4K color is 3840×2160. The 4K gray input is generated once by Pillow's RGB→L conversion from those exact 4K RGB bytes, then stored before any encoder timer begins. Synthetic inputs and their seeds are defined in `prepare_inputs.py`. Raw files have no header: byte-packed RGB or one byte per gray pixel.

Run correctness against the original and candidate public harnesses:

```powershell
python validate_encode.py --manifest inputs/manifest.json --reference ORIGINAL_HARNESS --candidate CANDIDATE_HARNESS --output fresh-validation --identity candidate_library=CANDIDATE_LIBRARY --identity entropy_source=CANDIDATE_CUH
python entropy_diagnostics.py fresh-validation
python validate_state.py --manifest inputs/manifest.json --reference ORIGINAL_STATE_HARNESS --candidate CANDIDATE_STATE_HARNESS --output state-validation
```

`validate_encode.py` requires exact complete JPEG bytes, actual RI0, equal quantizers and equal common-decoder pixels. The state runner performs 28 reused-state outputs plus 10 concurrent outputs from two preinitialized encoders/streams on two host threads. It compares concurrency to an isolated sequential original. The CPU entropy diagnostic independently parses the 78 synthetic outputs and checks padding, coefficient hashes and exercised coding boundaries. For the OFF smoke, select one manifest correctness case and run the same validator against a harness linked to the feature-disabled library.

Run the primary matrix only while the GPU and CPU are otherwise idle:

```powershell
python benchmark_encode.py --manifest inputs/manifest.json --baseline ORIGINAL_HARNESS --candidate CANDIDATE_HARNESS --nvjpeg NVJPEG_HARNESS --output benchmark --identity candidate_library=CANDIDATE_LIBRARY --identity entropy_source=CANDIDATE_CUH --identity nvjpeg_library=NVJPEG_DLL_OR_SO
```

The defaults are five seeded randomized alternating rounds, eight Q90 cases,110 calls per process and10 discarded warmups. The schedule is persisted before execution, and all timing samples are retained. Eight cases×three encoders×five processes×100 retained calls =12,000 warm samples. The eight cases are HD/4K RGB with444/422/420 and HD/4K gray. There are 120 fresh processes. The representative statistic is the median of five per-process medians; minimum/maximum process median show between-round variation. Do not mix Nsight or Compute Sanitizer into this run. They have separate evidence files and timing overhead.

Run the separate small-image diagnostic afterward:

```powershell
python latency_encode.py --manifest inputs/manifest.json --baseline ORIGINAL_HARNESS --candidate CANDIDATE_HARNESS --output latency
```

It measures gray1×1, gray32×32, RGB noise127×65 and RGB noise512×512 at 420, three rounds of 31 calls/discard 1. The two gray cases regress because GPU launch/scan overhead dominates; no CPU size threshold was introduced. Keep these observations separate from the natural-image matrix.

`summary.csv` contains compact performance values and five-round ranges. `quality.csv` contains sizes, quantizer equality, source MSE/PSNR and candidate byte equality. Detailed JSON records before/after source, library, executable, nvJPEG DLL and raw-input hashes. The final measured candidate executable was SHA256 `f11692097089eaba117fb7ee531eea5c954418461bc4233c746b71e3f7149692`; its GPUJPEG library was `1e440031b21c010450b55b00b2704bd077113b7352a34c92bf7fa680758dfea8`. The subsequent EOF-only source cleanup has separate byte/SASS equivalence evidence and does not replace these measured binary identities.
