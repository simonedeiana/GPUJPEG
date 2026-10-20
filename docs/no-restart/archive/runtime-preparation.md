> Archived investigation document. See the [current branch guide](../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# Runtime capture: no-restart decoder preparation

The tested Fastvideo executable does not directly divide the original no-restart
JPEG entropy stream among GPU threads. It first produces a **lossless JPEG entropy
transcode with restart interval16 in host memory**, then uploads the transformed
stream and runs its standard restart-aware GPU decoder.

## How this was observed

An original profiling helper (`capture.cpp`) uses NVIDIA's documented
`CUDA_INJECTION64_PATH` / `InitializeInjection` mechanism. It subscribes to CUPTI
runtime callbacks and, on API entry, records host-to-device source bytes and
Huffman kernel launch arguments. It touches only a newly spawned test process.
The instrumentation does not modify the target executable, CUDA arguments, or
uploaded bytes. Output file writes perturb timing, so the capture is structural
evidence, not a performance measurement.

Built using existing MSVC18 and CUDA13.4 CUPTI headers/library; successful
`cuptiSubscribe` and `cuptiEnableDomain` each return0 in `capture.tsv`.

The tested inputs are1920x1080,8-bit,444, interleaved sequential Huffman JPEG:

| Input | JPEG DRI | MCU count | Uploaded entropy bytes | Actual Huffman arg8 | Huffman launch |
|---|---:|---:|---:|---:|---|
| `work/no-restart.jpg` (GPUJPEG q90,r0) |0|32400|694474|16|8 blocks x256 threads|
| `work/profile.jpg` (Fastvideo q90) |10|32400|697456|10|13 blocks x256 threads|

In both captures, integer kernel arguments5,6,7,9 are65792,65792,32400,32416,
respectively; the16-byte MCU descriptor is
`01010100010101000101010000010200`. Argument8 is the per-thread restart-MCU
range identified independently in PTX. The launch sizes cover ceil32400/16=2025
and ceil32400/10=3240 active threads. Pointer argument values are process-specific.

Complete argument mapping from PTX and captured launch values:

| Index | Meaning | No-restart captured value | RST10 captured value |
|---|---|---|---|
|0|`rleToken_t*` token output|`0x1606bb6c00`|`0x1306bb6c00`|
|1|`decoderError_t*` error output|`0x1608f76880`|`0x1308f76880`|
|2|`uint32*` unstuffed/marker-free entropy input|`0x1608374c00`|`0x1308374c00`|
|3|`uint32*` partition end-bit-offsets|`0x1608f56e00`|`0x1308f56e00`|
|4|`huffmanState_t*` canonical Huffman tables|`0x1608f55c80`|`0x1308f55c80`|
|5|packed per-component DC table selectors|65792 (`00 01 01 00` LE)|same|
|6|packed per-component AC table selectors|65792 (`00 01 01 00` LE)|same|
|7|actual MCU count|32400|same|
|8|MCUs per independent partition|16|10|
|9|padded MCU pitch for token-plane stride|32416|same|
|10|16-byte `McuDesc`|`01010100010101000101010000010200`|same|

`McuDesc` bytes+8 are component block counts `01 01 01 00` and+12 are
component block prefixes `00 01 02 00`. Huffman PTX does not read fields+0/+4;
their interpretation is deliberately left unspecified here.

## Byte-level and coefficient-level proof

The original no-restart file is686167 bytes and its entropy starts at offset664,
so its original entropy payload is685503 bytes. The uploaded buffer is694474
bytes. Its2024 restart markers cycleFFD0..FFD7, with253 occurrences of each,
and it ends inFFD9. Its SHA256 is
`112a2337a2b7e3c2317869a6d4bbcf8ed93b583e808aa8867805e7b084913057`.

`coeff_compare.py` independently parses the input's SOF/DHT/SOS and its baseline
Huffman DC/AC coefficients. It decodes the original stream asDRI0 and captured
stream asDRI16. All97200 blocks x64 quantized coefficients are identical: zero
coefficient differences. The SHA256 over little-endian signed16 coefficients
in scan/block/zigzag order is
`970ef1ab0e07422c1688ba6df547f856cc1a3aa3e5bbc47b41dcf7961a1b0b01`.

The original requires5473457 coefficient bits; capturedDRI16 requires5505437
coefficient bits, before byte stuffing/padding/markers. Predictor resets change
DC difference values and coding lengths. It is not enough merely to insert a
marker into the original byte stream: legal MCU boundaries must be found,
segments padded, and DC differences at new segment starts adjusted.

A reconstructed JPEG keeps original headers, inserts DRI16 before SOS, and uses
the exact captured payload. Independent Pillow12.3.0/libjpeg decoding of the
original and reconstructed JPEG yields byte-identical1920x1080 RGB pixels;
SHA256 of either raw pixel image is
`a2f29ebd17edad844272593e5512b716ff6a9d406b2331e3ecf56097bdb7c906`.

For the input already carryingDRI10, the uploaded697456-byte payload is exactly
the original JPEG suffix beginning at entropy offset733. No host entropy
transformation is needed in this case.

## Interpretation and limits

This establishes the host-to-GPU protocol for this exact file and build. It
does not recover the host implementation or establish whether it traverses
all AC symbol lengths once, uses a specialized parser, or caches results.
The input preparation work necessarily happens before the captured upload;
the specific implementing native function and CPU cost are not established
by these callbacks. Do not generalizeDRI16 to every no-restart image or format.

The GPU still performs Huffman decoding after upload. Thus the behavior is
distinct from entirely CPU Huffman-decoding into an uncompressed coefficient
array, and distinct from arbitrary-offset speculative GPU Huffman decoding.

## Evidence files

- `no-rst/capture.tsv`, `rst10/capture.tsv`: kernel dimensions/arguments and copy sizes.
- `payload-analysis.json`: marker counts/locations/digests; no raw image bytes.
- `coefficient-comparison.json`: independent exact coefficient comparison.
- `pixel-comparison.json`: Pillow decoded-pixel equality.
- `provenance.json`: source and evidence file hashes.

Raw captured image bytes, original proprietary executable/DLLs, and compiled
profiling helper stay in the private working directory. The helper source and
analysis scripts are independently written and contain no proprietary code.

## Reproduction

Build the helper with `build.cmd` after adjusting its two explicit installed
toolchain paths if needed. It requires64-bit MSVC and an existing CUDA CUPTI
installation. There is no persistent profiler installation or environment change.

```
python run_capture.py --exe PATH_TO_ORIGINAL_FAST_JPEG_EXE --input NO_RST_JPEG --output NEW_CAPTURE_DIRECTORY
python coeff_compare.py --source NO_RST_JPEG --payload CAPTURED_ENTROPY_BIN --restart 16 --output-dir COMPARISON_DIRECTORY
```

The second command accepts the entropy-sized `h2d-NNN-SIZE.bin` found in the
first command's capture directory. Do not pick the bandwidth-test copies that
precede the final2052-byte table update. The comparison script is a deliberately
small independent parser for this baseline interleaved444 experiment, not a
general JPEG decoder or a proposed production implementation.
