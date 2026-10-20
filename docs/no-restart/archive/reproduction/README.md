> Archived investigation document. See the [current branch guide](../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# Build, verify, and reproduce

The patch targets CESNET/GPUJPEG commit
`78e5eee3f75e0a2a2e6c1409000ac05d679b4af8`. `GPUJPEG-source.zip` contains the
complete modified source, including upstream license notices. No Fastvideo
executables, DLLs, image fixtures, or full disassemblies are included.
The optional `go-gpujpeg` binding submodule is not vendored; its pinned gitlink is
recorded in the manifest and is not needed to build the CUDA library or CLI.

## Build

Use an existing CUDA-capable C/C++ build environment, CMake and Ninja. On Windows,
run from an x64 Visual Studio Developer Command Prompt. Adjust architecture86 for
your GPU. This investigation used CUDA13.4/MSVC14.51/RTX3050.

After extracting `GPUJPEG-source.zip`:

```
cmake -S GPUJPEG -B scalar -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=86 -DBUILD_SHARED_LIBS=OFF -DBUILD_OPENGL=OFF -DHUFFMAN_GPU_WARP_SERIALIZATION=OFF
cmake --build scalar --parallel 4
cmake -S GPUJPEG -B warp -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=86 -DBUILD_SHARED_LIBS=OFF -DBUILD_OPENGL=OFF -DHUFFMAN_GPU_WARP_SERIALIZATION=ON
cmake --build warp --parallel 4
```

The source includes decoder caching in both builds. The encoder optimization is
OFF by default and needs CUDA9+. It falls back to the scalar path on pre-SM3 GPUs.

Alternatively, apply the patch to a clean checkout of the pinned upstream commit:

```
git clone https://github.com/CESNET/GPUJPEG.git GPUJPEG
git -C GPUJPEG checkout 78e5eee3f75e0a2a2e6c1409000ac05d679b4af8
git -C GPUJPEG apply --check ../gpujpeg-fastjpeg-techniques.patch
git -C GPUJPEG apply ../gpujpeg-fastjpeg-techniques.patch
```

## Encoder parity

The portable test requires Python3 and a GPU, without image libraries:

```
python GPUJPEG/test/compare_huffman_serializers.py scalar/gpujpegtool.exe warp/gpujpegtool.exe
```

Remove `.exe` on Linux. Add `--image your-image.ppm` to extend the generated
fixture suite. It compares complete JPEG bytes, including padding and markers.
The generated suite passed49cases; a separate actual-image suite passed34cases.

Compute Sanitizer's new-kernel checks can be reproduced with:

```
compute-sanitizer --tool memcheck --kernel-name kns=gpujpeg_huffman_encoder_serialization_kernel_warp warp/gpujpegtool.exe -e -q 100 -i --subsampled=420 -r 8 input.ppm output.jpg
```

Repeat with `--tool racecheck` and `--tool synccheck`. The recorded runs varied
restart/subsampling and targeted only the new kernel; they are not an exhaustive
sanitizer audit of upstream GPUJPEG.

## Decoder cache regression

Pillow is used only to create JPEG fixtures with optimized and ordinary Huffman
tables. Use a version supporting `restart_marker_blocks`; the generator checks
that the resulting file actually contains DRI7.

```
python GPUJPEG/test/make_huffman_cache_fixtures.py fixtures
```

Windows Developer Command Prompt, with CUDA_PATH set by the installed toolkit:

```
cl /nologo /O2 /MD /DGPUJPEG_STATIC /I GPUJPEG /I scalar /I "%CUDA_PATH%\include" GPUJPEG\test\huffman_table_cache.c scalar\gpujpeg.lib /link /LIBPATH:"%CUDA_PATH%\lib\x64" cudart.lib /OUT:huffman_table_cache.exe
huffman_table_cache.exe fixtures/a.jpg fixtures/b.jpg fixtures/c.jpg fixtures/gray.jpg
```

Linux equivalent (adjust toolkit path):

```
cc -O2 -DGPUJPEG_STATIC -I GPUJPEG -I scalar -I /usr/local/cuda/include GPUJPEG/test/huffman_table_cache.c scalar/libgpujpeg.a -L /usr/local/cuda/lib64 -lcudart -lstdc++ -lm -ldl -lpthread -o huffman_table_cache
./huffman_table_cache fixtures/a.jpg fixtures/b.jpg fixtures/c.jpg fixtures/gray.jpg
```

Repeat the build with `-DHUFFMAN_GPU_CONST_TABLES=ON`, linking the harness against
that build. The harness alternates DHT contents and two decoder instances.
Pass additional no-restart JPEGs to test the CPU fallback. Six inputs produced
90successful alternating decodes in each tested configuration. The raw validation
JSON also contains separate baseline/patched output hashes.

## Measuring changes

For the encoder's isolated change, the scalar and warp builds above are sufficient.
For the decoder cache comparison, also build the **unpatched pinned commit**;
turning the serializer OFF does not disable caching.

```
python benchmark.py scalar/gpujpegtool.exe warp/gpujpegtool.exe input.ppm --codec encode --output encode-results.json -- -q 90 -i --subsampled=420 -r 10
python benchmark.py upstream/gpujpegtool.exe scalar/gpujpegtool.exe input.jpg --codec decode --output decode-results.json
```

Timings come from GPUJPEG's verbose per-iteration output, not process wall time.
Defaults:3rounds,110iterations, first10discarded. Runs alternate baseline/candidate
order. Saved samples allow checking variability. No process/file I/O timing is
presented as codec latency. Keep concurrent GPU work out of these comparisons.

For an independent GPU trace:

```
nsys profile --trace=cuda --sample=none -o gpujpeg-encode warp/gpujpegtool.exe -e -vv -n 110 -q 90 -i --subsampled=420 -r 10 input.ppm output.jpg
nsys stats --report cuda_gpu_kern_sum gpujpeg-encode.nsys-rep
```

## Fastvideo investigation

Use your original attachment locally. REA commands used were version-pinned:

```
npx --yes rea-agents@6.7.0 inspect-artifact PATH_TO_FAST_JPEG_ZIP --json
npx --yes rea-agents@6.7.0 execute-rizin-command PATH_TO_FAST_JPEG_EXE iIj --json
cuobjdump --dump-ptx PATH_TO_FAST_JPEG_EXE
cuobjdump --dump-sass --gpu-architecture sm_86 PATH_TO_FAST_JPEG_EXE
```

For native commands, set REA_RIZIN_COMMAND to the absolute path of an existing
Rizin executable. Rizin metadata output alone does not reconstruct algorithms;
the report combines PTX/SASS, runtime kernels, captured arguments and independent
coefficient checks.

`runtime-capture/` contains the original CUPTI helper source and checker. Its
README instructions are in `../runtime-preparation.md`. The helper applies only
to a newly spawned process, via child-only environment variables. It records
selected host buffers and arguments without altering target bytes/arguments.
Capture writes intentionally perturb timing: use it for structural evidence only.
Reproduce the coefficient test on an interleaved8-bit444 baseline JPEG, then give
the entropy-sized capture to `coeff_compare.py --restart 16`. The checker is
deliberately limited to this experiment, not a hardened general JPEG parser.

The decoder appendix's PTX line coordinates refer to local investigative extracts;
their extraction script is included here. Complete proprietary PTX stays in the
original working directory and can be regenerated from the user's binary. Compact
identities and runtime facts are in `../evidence/`.

```
python extract.py cuobjdump-output.ptx decoder-extracts
```

The upstream shell/make-based CTest regression suite was not run on this Windows
host. Validation used the targeted GPU suites described above. Broader devices,
CUDA versions, larger image corpora and concurrent-stream behavior remain untested.
