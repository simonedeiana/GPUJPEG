> Archived investigation document. See the [current branch guide](../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# GPUJPEG encoding without restart markers

The port is **7.7–13.0× faster than original GPUJPEG on the eight tested HD/4K configurations**, including input upload and complete JPEG retrieval. It preserves original GPUJPEG's complete JPEG bytes. Its median latency remains **2.3–12.7% higher than nvJPEG's**. Tiny images can regress because of kernel launch overhead.

The active source contains this encoder change only. Earlier decoder and serializer experiments remain recoverable in their previous source archives, patches and reports.

## Transfer-inclusive performance

| Input / sampling | Original GPUJPEG | New GPUJPEG | nvJPEG | Speedup over original |
|---|---:|---:|---:|---:|
| 1080p 4:4:4 | 30.081 ms | 2.890 ms | 2.656 ms | 10.41× |
| 1080p 4:2:2 | 23.647 ms | 2.603 ms | 2.492 ms | 9.09× |
| 1080p 4:2:0 | 20.464 ms | 2.470 ms | 2.416 ms | 8.28× |
| 1080p gray | 15.492 ms | 1.188 ms | 1.146 ms | 13.04× |
| 4K 4:4:4 | 113.753 ms | 10.524 ms | 9.490 ms | 10.81× |
| 4K 4:2:2 | 84.874 ms | 9.394 ms | 8.603 ms | 9.04× |
| 4K 4:2:0 | 68.737 ms | 8.911 ms | 8.383 ms | 7.71× |
| 4K gray | 50.601 ms | 3.944 ms | 3.499 ms | 12.83× |

Values are medians of five round medians, each from 100 warm calls after discarding 10 initial calls. The comparison contains 120 separate processes and 12,000 retained samples. Every timed call starts with the same packed, pageable host pixels and ends with the complete host JPEG after stream synchronization. Recurring CPU formatting is included. Context creation, setup/allocation and disk I/O are outside the warm measurements. Stage timers are disabled; codec and case order are randomized using a recorded seed, with exclusive GPU execution.

Settings are baseline JPEG, quality 90, standard Huffman tables, interleaved color and actual restart interval zero. The eight configurations use four inputs: the supplied 1920×1080 RGB, 3840×2160 RGB and 1920×1080 gray files, plus a 4K grayscale image derived once outside all timers. Every output was audited for SOI/SOS/EOI, absent or zero DRI, and no RST markers. Binaries and input hashes remained unchanged throughout.

[benchmark.csv](benchmark.csv) retains every round's median/range, output size and quality. [Raw benchmark metadata](evidence/benchmark/summary.json) and individual timing logs support the table. Small differences should be assessed against the recorded round ranges.

Measured system: RTX 3050 8 GiB, negotiated PCIe Gen3 ×8, Xeon E5-1650 v4, Windows 10 build 19045/WDDM, driver 617.42, CUDA 13.4.59, MSVC 14.51, release SM86 builds. Installed nvJPEG DLL version: 6.14.11.1322; SHA-256 `957bb73818e62883ac4bfc0aa15204bba5e341a8f7964a11b0289742df4e0da2`.

## How nvJPEG does it

REA 6.7.0/Rizin inspection, embedded CUDA PTX, SM86 machine code and actual CUDA traces establish the mechanism. The harness explicitly selects the GPU encoder backend, baseline DCT, standard tables and restart interval zero. NVIDIA documents zero as the default restart interval; nonzero interval control is specific to hardware encoding. [NVIDIA documentation](https://docs.nvidia.com/cuda/nvjpeg/)

An encoder already knows the quantized coefficients and block boundaries. It computes each block's DC difference by reading the preceding same-component block's raw DC coefficient. Every block can independently generate its Huffman bits and length `L[b]`. The exclusive prefix sum `O[b] = sum(L[j], j < b)` gives that block's exact start bit in the final continuous stream.

The observed nvJPEG path uses warp-parallel AC coding, independent DC coding, hierarchical 64-bit length scans in JPEG order, and shifted atomic-OR assembly into a cleared output. Atomic OR merges disjoint contributions when blocks share a word. It pads only the scan's final byte. A second scan counts valid four-byte groups plus extra bytes needed after `FF`; a parallel scatter writes the bytes and inserted zeros. Restart markers are unnecessary for this allocation scheme.

Runtime traces observed these stages for every profiled frame. This establishes parallel marker-free **encoding**; it does not establish a marker-free decoding algorithm.

[analysis.md](analysis.md) gives exact private instruction coordinates, executed launch shapes, transfer-completion proof, REA Evidence IDs and unresolved details. [Focused REA evidence](rea-evidence-summary.json) retains target/record hashes, commands and selected facts. Full vendor code remains private. The port is independently written and does not link nvJPEG into GPUJPEG.

## What changed in GPUJPEG

Original GPUJPEG downloads the full quantized coefficient image and performs serial CPU Huffman coding when `restart_interval=0`. The new path:

1. Uses eight warps per CTA, one warp per 8×8 block. Each lane processes two zigzag coefficients with GPUJPEG's existing RLE/Huffman convention. The first lane obtains DC directly from its predecessor. A warp scan and shared atomic OR form the private block fragment.
2. CUB-scans block lengths, then shifts and merges private words into a cleared continuous scan using global atomic OR.
3. Pads once per scan, counts stuffing in 256-byte tiles, scans tile output sizes, then writes bytes and `FF 00` pairs to disjoint destinations.
4. Downloads compressed entropy bytes, their size and segment descriptors. Existing CPU header/SOS/EOI formatting produces the final host JPEG.

DC follows component/MCU coding order. Separate component scans reset DC prediction and padding independently. Positive restart intervals retain the original route.

`HUFFMAN_GPU_NO_RESTART` defaults ON with CUDA 11+. Supported execution requires SM3+ and at most 2,097,151 coded blocks; disabled, older or oversized configurations retain CPU encoding. Only the encoder CUDA unit requests C++17 for CUB. Additional per-instance GPU storage is `272*N + 4 + CUBTemp` bytes for maximum scan capacity N, approximately 25.2 MiB for HD444 and 100.9 MiB for 4K444, beyond existing codec allocations. Allocation failure returns an error.

[implementation.md](implementation.md) describes memory and lifecycle. [implementation-review.md](implementation-review.md) supplies source line anchors and proofs of DC mapping, bounds, bit merging and stuffing.

## Validation

- 86 fresh configurations reproduce original GPUJPEG's complete bytes and decoded pixels, covering grayscale/444/422/420, interleaved/separate scans, tiny/odd/flat/checker/noise/natural inputs and qualities 0/1/75/90/100.
- 28 same-instance outputs exercise tiny-to-4K scratch growth, shrinking, layout/quality changes and RI0 ↔ RI1/8/10. Ten outputs from two preinitialized encoders on separate streams/host threads match isolated original output. The feature-OFF build also passes.
- An independent CPU entropy parser validates 78 small outputs: all 0–7 padding lengths, all 32 block-start bit offsets, 4,534 actual 16-bit Huffman symbols, 919 ZRL symbols and DC differences up to 2040. The fresh matrix exercises 27,079 stuffing pairs, all 256 positions within a tile, boundary FFs and a terminal FF requiring a zero.
- Final memcheck and synccheck report zero errors. Whole-pipeline racecheck reproduces 72 warnings in unchanged upstream DCT code. Excluding only that DCT kernel, all new entropy and CUB kernels report zero hazards, errors or warnings. The entire encoder is not described as racecheck-clean.

After measurement, one redundant final LF was removed from each of two new files. A rebuild produces byte-identical complete SM86 SASS, with all 902 function bodies matching. A final JPEG smoke also matches. Both measured and delivered identities, exact whitespace-equivalence proof and the instruction comparison are retained; the full benchmark was not repeated for this formatting-only change.

## Quality and remaining limits

All component quantization tables match nvJPEG in the eight benchmark cases. The port preserves original GPUJPEG bytes, so its acceleration does not reduce that encoder's quality. nvJPEG reconstructs slightly different pixels: PSNR is nearly equal for 444/gray and up to about 0.31 dB higher for 420 here. Color conversion, downsampling or rounding differences are plausible causes, not established causes. The comparison matches settings rather than requiring identical reconstruction; sizes, MSE and PSNR are retained in the CSV and metadata.

Across four paired profiling cases, the new entropy path including initialization still takes 1.25–1.75× nvJPEG's GPU entropy time. Coding and assembly account for most of the observed gap. No hardware-counter bottleneck attribution is claimed. Transfers narrow the full host-latency difference: profiled 4K RGB upload alone takes about 6.16 ms here. Profiled stage times are diagnostics, not substitutes for the unprofiled table.

Tiny-image launch overhead is real: gray 1×1 changes from 0.06785 to 0.16350 ms, and gray 32×32 from 0.07145 to 0.20680 ms. Noise 127×65 420 improves 0.29770 → 0.26625 ms; noise 512×512 420 improves 6.38320 → 0.62685 ms. These are separate three-round, 31-call diagnostics. No small-image threshold was added. Disabling the CMake option restores CPU RI0 encoding.

## Delivery and cleanup

Source archive (`artifact: GPUJPEG-no-restart-source.zip`), upstream patch (`artifact: gpujpeg-no-restart.patch`), Windows SM86 tool (`artifact: gpujpegtool.exe`), [build and reproduction instructions](README.md). The patch is checked against upstream commit `78e5eee3f75e0a2a2e6c1409000ac05d679b4af8`; every packaged source file is compared after application.

Earlier experiments were removed from the active source after preserving their packages and evidence. Cleanup removed 8,776,105,065 bytes of regenerable old dumps and consolidated 6,367 other scratch files into a verified archive. Original software/images and the CUDA installation were preserved. [Cleanup record](../cleanup/README.md).
