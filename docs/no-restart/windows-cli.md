# Windows raw RGB command-line fix

The Windows console target must define both `UNICODE` and `_UNICODE`.
`UNICODE` selects the Windows SDK's wide `TCHAR`; `_UNICODE` selects the CRT's
wide `_t*` functions and this application's `wmain`/wide getopt implementation.
Previously CMake defined only `_UNICODE`. Windows therefore declared `TCHAR`
as `char`, while `_tcsstr` and `_tstoi` operated on `wchar_t` strings. In the
size parser, `pos + 1` advanced one byte beyond the wide `x`, rather than one
wide character, so the height parsed as zero. This rejected headerless RGB
encoding with “Image dimensions must be set to nonzero values!”. It also
produced incompatible-pointer compiler warnings.

The fix uses `target_compile_definitions(gpujpeg-bin PRIVATE UNICODE _UNICODE)`
for the Windows console target. It applies consistently to `main.c` and the
bundled getopt implementation. No entropy, transform, library ABI, or public API
code changes. The encoder lock tag remains unchanged.

## Build with both restart-free GPU paths

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=86 -DBUILD_OPENGL=OFF -DBUILD_SHARED_LIBS=OFF -DHUFFMAN_GPU_NO_RESTART=ON -DHUFFMAN_GPU_NO_RESTART_DECODER=ON
cmake --build build --config Release
```

Use a Visual Studio x64 tools prompt with CUDA on PATH. Set the architecture
appropriately for your GPU. The normal source decoder default remains OFF;
the explicit flag above enables it in the supplied combined Windows build.

For packed RGB24 (R,G,B bytes, no row padding/header):

```sh
gpujpegtool.exe --decode --colorspace=rgb --pixel-format=444-u8-p012 original.jpg decoded.rgb
gpujpegtool.exe --encode --size=512x480 --colorspace=rgb --pixel-format=444-u8-p012 --quality=95 --restart=0 --subsampled=4:4:4 --interleaved decoded.rgb reencoded.jpg
gpujpegtool.exe --decode --colorspace=rgb --pixel-format=444-u8-p012 reencoded.jpg redecoded.rgb
```

The encoder dimensions must match the raw input. For decoding, the restart
interval is a property of the input JPEG, not a decoder option.

## Regression coverage

```sh
python test/regression/windows_cli.py --tool build/gpujpegtool.exe --out new-cli-test-output
```

The test needs a CUDA-capable GPU and only Python's standard library. It creates
an odd-sized 37x19 RGB24 fixture and retains logs, JPEGs and decoded RGB output.
It exercises long options with equals/separate arguments, short options,
quality 95 versus 70, RI0 versus RI8, equivalent-output comparisons, Unicode
and space-containing paths, and multiple input/output pairs. A marker parser
checks actual dimensions, quantizers and entropy restart framing. It verifies
that each decoded raw image has exactly width * height * 3 bytes.

On Windows/RTX 3050/CUDA 13.4/MSVC 14.51, the pre-fix executable fails the first
encode with the zero-dimension message. The fixed executable passes all cases.
The three downloaded baseline JPEGs in the local demonstration additionally
exercise real JPEG → RGB24 → JPEG → RGB24 processing, with RI0 marker audits
and comparison against the separately validated public-API runner. These
functional runs do not constitute new performance benchmarks.
