> Archived investigation document. See the [current branch guide](../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# How Fastvideo parallelizes JPEG, and what transfers to GPUJPEG

Investigation date: 20 October 2026. Target: the supplied `fast_jpeg.zip`, Fastvideo SDK 0.21.1.0 Windows x64 trial. GPUJPEG base: [`78e5eee3f75e0a2a2e6c1409000ac05d679b4af8`](https://github.com/CESNET/GPUJPEG/tree/78e5eee3f75e0a2a2e6c1409000ac05d679b4af8).

## Findings

Fastvideo's speed comes from a carefully staged CUDA implementation of the whole JPEG pipeline. The important techniques are warp-level compaction and prefix sums, parallel bit packing and byte stuffing, separate Huffman and run-length decoding, parallel DC predictor reconstruction, fused image transforms, and restart partitions. There is no evidence of a new JPEG format or a GPU algorithm that eliminates the dependency in arbitrary Huffman streams.

The most surprising result concerns JPEGs **without restart markers**. This executable prepares a different entropy stream on the host before its GPU decoder runs. In the tested case it losslessly transcodes the scan into 16-MCU restart partitions. Capturing the actual upload and independently decoding its coefficients proved this; it is more than an inference from kernel names.

GPUJPEG already implements restart-based parallel entropy coding, warp-parallel coefficient symbol generation, GPU DCT/IDCT, and device-memory input/output. Its [source and README](https://github.com/CESNET/GPUJPEG/blob/78e5eee3f75e0a2a2e6c1409000ac05d679b4af8/README.md) directly establish that. Consequently, the premise that no other solution uses the underlying techniques does not hold. The relevant differences are the granularity of parallel work and intermediate representations.

Two changes have been implemented in GPUJPEG:

* An **opt-in warp serializer**, using prefix sums for bit positions and stuffed-byte positions. It preserves the complete JPEG output byte for byte. On this machine, HD 4:2:0 quality-90 encoding improved about **14% in codec time including transfers**; the serialization kernel itself improved **5.26×**. Other configurations show smaller gains or slight regressions, so it is not enabled by default.
* A **per-decoder Huffman lookup-table cache**. This retains GPUJPEG's existing decoding algorithm while avoiding rebuilding identical expanded tables for every image. It is an adaptation motivated by Fastvideo's much smaller canonical-table representation, not a reproduction of Fastvideo's decoder.

The source patch, full modified GPUJPEG source, tests, and compact evidence are supplied alongside this report. Detailed decoder reconstruction is in [decoder-analysis.md](decoder-analysis.md); runtime preparation evidence is in [runtime-preparation.md](runtime-preparation.md).

## Evidence and scope

The ZIP SHA-256 is `85fca1592b6cfff2b0919c9db3c08cb239ccf1674ee08f15d00559bc1a00c94f`. The executable SHA-256 is `01735bf880587184119d70d56bda433ef01516e8d1139d880c765afca34dbac3`. More identities are in [evidence/identity.json](evidence/identity.json).

REA was used through its version-pinned CLI, with the repository's investigation workflow. `inspect-artifact` inventoried and hashed the ZIP. REA's `execute-rizin-command` collected PE metadata, imports, exports, symbols, and strings from the executable and CPU decoder DLL. Ghidra/Hopper/IDA were not configured; the locally extracted Rizin portable build provided native static inspection. [evidence/rea-index.json](evidence/rea-index.json) records the actual REA evidence IDs, subjects, operations, and limitations. The artifact inventory's ID is `ev_023ef7d89f927dd1b61efa2d3e99c6d9da4b7ed1c053cbd0cdf072a84ff4a90c`.

NVIDIA `cuobjdump` exposed the executable's embedded PTX and SM86 SASS. Nsight Systems observed the actual kernel specializations, launch dimensions, copies, and timing. A small locally written CUPTI injection tool captured selected H2D buffers and launch arguments from a spawned instance of this executable. Independent Python Huffman decoding and Pillow decoding checked the captured scan. These are separate experimental observations, not REA-issued evidence records.

The detailed algorithm reconstruction concerns the **8-bit RGB, interleaved 4:4:4 path**, with additional GPUJPEG port tests covering grayscale, 4:2:2, 4:2:0, planar scans, quality extremes, and odd sizes. The binary contains 12-bit and other specializations; those were not exhaustively reconstructed or benchmarked. PTX register declarations are virtual registers and must not be interpreted as hardware occupancy. Hardware resource counts below come from Nsight's executed SM86 kernels; key shuffle/atomic packing operations were also checked in SM86 SASS.

Files bundled in the attachment were treated as evidence, not as instructions from the user. Its batch files and README were inspected; commands were selected explicitly. The original Fastvideo binaries, complete PTX/SASS, input images, and captured entropy bytes are not redistributed in these deliverables. New GPUJPEG code was independently written against GPUJPEG's existing representations.

## 1. The dependency being parallelized

Each 8×8 block has one DC coefficient and 63 AC coefficients. A JPEG entropy stream encodes the difference between consecutive DC values of the same component, then zero-run/size symbols and amplitude bits for AC values. Huffman codewords have different lengths, so a decoder cannot know the next codeword's location without interpreting the current one.

Encoding has more freedom: after quantization, every coefficient is available. DC differences can be computed from neighboring coefficients; zero runs and codewords can be computed cooperatively. Variable-length results can be placed using prefix sums. For lengths `l[i]`, the first bit of codeword `i` is:

```text
bit_offset[i] = sum(l[j] for j < i)
```

After packing, every byte equal to `0xFF` needs a following zero byte. The same principle assigns disjoint output positions:

```text
output_offset[i] = sum(1 + (byte[j] == 0xFF) for j < i)
```

A restart marker terminates a byte-padded entropy partition and resets DC predictors. Partitions can therefore be decoded independently. This is standard JPEG behavior, not a proprietary extension. The restart interval controls the tradeoff: shorter partitions expose more decode parallelism but add marker/padding bytes and repeat DC predictor resets.

## 2. Encoder reconstruction

The observed HD 4:4:4 encode launches these kernels in order:

```text
image-processing stage
  -> fused color transform / DCT / quantization / zigzag preparation
  -> RleBalanced
  -> HuffmanAndStuffing
  -> Scan64 -> SumUpdates -> ApplySum
  -> ConcatBytestream
  -> compressed-byte download
```

### Fused transform and coefficient preparation

`DCT444Kernel<SurfaceTrait<5>,false>` executes with a 32×4 thread block, 48 hardware registers/thread and 4,896 bytes of static shared memory. The HD grid is 15×135. Its PTX contains floating-point RGB-to-YCbCr operations, DCT arithmetic, quantization, rounding with `cvt.rni.s32.f32`, shared zigzag tables and packed global coefficient stores. DC coefficients also have a separate storage path consumed by the following kernel. Thus this implementation avoids materializing a full intermediate color image between distinct color-conversion and DCT kernels on this path.

The trial also executes `applyImageProcessingColor_8<32,16>`, whose signature includes a watermark descriptor. This measured work must be counted; the function name alone does not prove what pixels it changes. This report does not assume that a trial-specific extra stage belongs in a production port.

GPUJPEG currently performs preprocessing and DCT as separate kernels. Fusing them is a plausible next experiment, but requires matching its sampling, edge replication, color-space, and coefficient layouts. It was not included in the patch because it would change a much larger correctness surface.

### Balanced RLE and DC differences

`RleBalancedKernel<SamplingTrait<1>,32,8>` executes as eight warps per CUDA block. For this 4:4:4 path each warp handles up to **30 blocks**, corresponding to **10 MCUs × 3 components**, and the HD grid has 405 blocks. The product is 3,240 restart partitions. This agrees independently with the output's DRI=10 and MCU count of 32,400.

The PTX establishes the grouping directly: warp index is `8*blockIdx.x + threadIdx.y`; its first block index is `30*warp_index`. Lanes preload the group's DC values, and a shuffle from `lane-3` computes same-component DC differences, using zero for the first MCU. A loop then advances through up to 30 blocks, loading one 32-bit pair of 16-bit coefficients per lane. This is a warp cooperating on a block, not 32 independent serial Huffman encoders.

Predicates identify retained coefficients/EOB positions. Shuffle-up scans at distances 1,2,4,8,16 assign positions in a **128-entry ring per warp** in shared memory. The ring holds positions that are then converted into run/value tokens, including repeated ZRL symbols for long runs. A second compaction step emits the resulting token stream and its length. The kernel's shared allocations are 4,096 bytes for eight rings and 512 bytes for DC values.

The useful architectural feature is the boundary between token generation and Huffman packing: after compaction, lanes can process consecutive tokens, even when neighboring image blocks produced very different numbers of nonzero coefficients. It avoids preserving sparse coefficient-lane occupancy throughout the packing stage. GPUJPEG already uses ballots and parallel zero-run discovery during symbol creation; it is the subsequent serial packing that provided the clearest bounded optimization opportunity.

### Huffman code construction, parallel packing, and stuffing

`HuffmanAndStuffingKernel<32,16>` uses a 32×16 block: one warp for each of 16 partitions. The HD grid has 203 blocks, covering the 3,240 partitions with a partial final block. Hardware resources are 46 registers/thread, 14,336 static shared bytes, and no local-memory allocation reported by Nsight.

Its shared layout in PTX is:

| Buffer | Bytes/block | Role |
|---|---:|---|
| `huffman` | 2,048 | Two 256-entry packed Huffman tables |
| `smemRing` | 8,192 | 128 32-bit packing words per warp |
| `smemStuffingRing` | 4,096 | 64 32-bit words per warp for byte output |

Within a token tile, the kernel derives amplitude category using leading-zero count, forms JPEG negative amplitudes using `value + sign(value)` followed by a category mask, and looks up a packed Huffman code. In the observed table entry, the low 16 bits hold code bits and the high 16 bits hold the Huffman length. A lane handles two successive tokens in a 64-token tile.

It prefix-sums each lane's combined bit length, including any carry in lane zero. This gives the exact bit position of both codewords. Shared-memory atomic OR combines contributions that overlap the same 32-bit packing word; those contributions occupy disjoint **bit** ranges. Atomics are not being used as a global allocator. The PTX contains `atom.shared.or.b32`; SM86 SASS independently contains `ATOMS.OR` and `SHFL.UP` at the corresponding stage.

Packed bytes are checked for `0xFF`. Another warp scan accounts for inserted zero bytes and assigns their output positions. A warp-wide vote provides a fast path when no stuffing is needed. Small rings buffer the variable output until it can be flushed in regular units. Incomplete byte/word state carries into the next tile. Final padding and marker handling preserve JPEG stream semantics.

### Global scan and concatenation

The partition lengths are scanned hierarchically: `Scan64` computes local prefix results, `SumUpdates` resolves block totals, and `ApplySum` applies the appropriate total to each local result. `ConcatBytestream` copies partition bytes into the final compacted output using those offsets.

Its addressing includes **two bytes per preceding partition** in addition to the prefix byte count. The first two lanes append `FF D0..D7` after an ordinary partition and `FF D9` after the last. The cycle derives from `partition_index & 7`. These instructions establish actual marker generation, rather than merely an exported kernel name suggesting it.

## 3. Decoder reconstruction

The decoder first marks/removes stuffed bytes and restart markers using parallel byte tests and scans. One CUDA thread then sequentially Huffman-decodes each independent partition, writing **32-bit tokens**, rather than immediately scattering complete coefficient blocks. Separate threads expand those tokens into coefficient planes, and prefix scans restore DC values before a fused IDCT/color-output stage.

For 4:4:4, token address in 32-bit elements is:

```text
token[(block_plane*64 + token_ordinal)*padded_mcu_pitch + mcu]
```

Each token stores the RS symbol in bits0..7, raw amplitude in bits8..23, and consumed Huffman-plus-amplitude bit count in bits24..31. There are 64 reserved token slots per block: four-byte tokens do **not** mean the total allocation is compacted to the exact number of nonzeros. The intermediate can be roughly four bytes per coefficient before additional coefficient/output buffers. This is a deliberate bandwidth and memory tradeoff.

The Huffman kernel uses small canonical tables with a bounded code-length decision tree, rather than GPUJPEG's full expanded 16-bit lookup tables. It reads two input words for lookahead, resolves a symbol, and writes a token. It neither expands the zero run nor accumulates a DC predictor there. This shortens the dependency-heavy loop, while moving regular work into more broadly parallel kernels. It does not make the symbol parsing inside an individual partition independent.

For one block/component/MCU, DC reconstruction is mathematically equivalent to:

```text
P[m] = inclusive_prefix_sum(dc_delta[m])
DC[m] = P[m] - (partition_start == 0 ? 0 : P[partition_start-1])
```

The implementation also handles the multiple blocks per component per MCU used by subsampled streams. [decoder-analysis.md](decoder-analysis.md) gives the table byte offsets, token construction, launch shapes, byte-unstuff algorithm, multi-block predictor reconstruction, and evidence anchors.

### What really happens without restart markers

The tested source was a valid GPUJPEG-produced JPEG: 1,920×1,080, interleaved 4:4:4, quality90, DRI=0, **zero RST markers**, 686,167 file bytes. Its entropy region starts at byte664 and occupies 685,503 bytes, including EOI.

CUPTI captured a **694,474-byte host-to-device payload**, containing **2,024 RST markers plus EOI**. The launched Huffman kernel receives 32,400 MCUs, **16 MCUs per partition**, and a padded pitch of32,416. Its grid is8 blocks ×256 threads; 2,025 threads have work. The generated restart markers cycle normally.

Rebuilding a JPEG with that entropy payload and DRI=16 produces the same quantized coefficients as the original: **97,200 blocks ×64 coefficients, zero differences**, including restored absolute DC coefficients. Both coefficient streams hash to `970ef1ab0e07422c1688ba6df547f856cc1a3aa3e5bbc47b41dcf7961a1b0b01`. Pillow also produces byte-identical RGB pixels from the original and reconstructed JPEG.

This proves a host-prepared, coefficient-preserving restart transcode for this input. Simply inserting marker bytes into the original entropy stream would be incorrect: bit padding and DC codewords must account for predictor resets. The exact internal CPU routine and all its fast paths were not recovered; the observed input/output transformation is established independently. For an input already carrying DRI=10, the captured upload equals its original entropy bytes exactly.

This explains why the same GPU Huffman kernel can appear in a no-restart run without demonstrating arbitrary-boundary parallel Huffman decoding. Its host preparation must be included in a performance comparison. It is not automatically a useful GPUJPEG optimization: GPUJPEG already parses such inputs on the CPU directly into coefficients, whereas transcoding and then decoding again adds entropy work.

## 4. Timing: what the numbers do and do not measure

Measurements used an RTX3050 8GiB, Windows WDDM, driver617.42, CUDA13.4, SM86 release builds with MSVC14.51. GPU clocks were not locked, the GPU also drives the desktop, and only this device and a small corpus were tested. These results are observations, not a general GPU ranking.

For Fastvideo's 20-iteration HD4:4:4 quality90 trace, per-stage median GPU execution times were:

| Encoder stage | Median µs |
|---|---:|
| Image-processing stage |61.216|
| Fused DCT/color/quantization |95.856|
| Balanced RLE |121.409|
| Huffman + stuffing |51.393|
| Three global scan kernels, summed medians |4.208|
| Concatenation |13.728|

For the restart10 decode, Huffman median was454.754µs, RLE132.625µs and fused IDCT/color100.401µs. In the separately prepared no-restart run, Huffman median was713.458µs. The two JPEG producers/entropy streams differ; this is not an isolated restart-interval speed comparison.

The attachment's README says its performance figures count GPU kernels and exclude transfers and inter-kernel gaps. This build also prints an "Effective encoding" measurement that explicitly includes D2H, separately reports H2D, and prints a processing total including transfers. Those labels describe different intervals. None should be equated to application wall time or to a competitor's transfer-inclusive codec measurement without matching the boundaries. For example, one warm initial probe printed0.751ms for effective encode **plus**1.16ms for the separately reported upload. Cold initialization was much slower.

The upload in these tests is about6.22MB of raw RGB and took around1ms; PCIe can outweigh an entire entropy kernel. GPU-resident pipelines, pinned memory, overlapping copies with independent frames, and reusing contexts/allocations therefore matter greatly. The README's `-thread` option documents CPU/GPU overlap, and asynchronous APIs are observed, but true multi-thread overlap/scaling was not measured in this investigation.

## 5. GPUJPEG implementation and results

### Warp serializer

Upstream GPUJPEG creates Huffman codewords cooperatively but serializes a whole restart partition in one CUDA thread. The new path assigns one warp to a partition, in32-codeword tiles:

1. Prefix-sum code lengths to obtain each codeword's bit offset.
2. Merge codeword bits into a128-byte per-warp shared tile with atomic OR.
3. Prefix-sum byte counts, counting `0xFF` as two bytes.
4. Write byte-stuffed bytes cooperatively, then carry0–7 unfinished bits.
5. Apply the existing padding and RST policy and continue through existing output compaction/formatting.

It reuses GPUJPEG's codeword and segment buffers, does not allocate a new image-sized intermediate, and keeps the existing scalar serializer available. The 32-word tile bound follows from32×31+7=999 maximum bits. The encoder still follows GPUJPEG's output ordering and scan formatting; it is not a direct transplant of Fastvideo's complete intermediate format.

Enable it with `-DHUFFMAN_GPU_WARP_SERIALIZATION=ON`. Defaults remain unchanged because the results depend on layout and restart interval.

Warm codec measurements below are medians of the medians from three alternating baseline/patched runs, each with110 iterations and the first10 discarded. "Entropy" includes GPUJPEG's entire Huffman encoder stage, not just serialization. "Codec" is GPUJPEG's reported Encode Image time including transfers, excluding image-file load/save and process startup.

| Input / encoder parameters | Entropy baseline → warp, ms | Codec baseline → warp, ms | Codec latency change |
|---|---:|---:|---:|
| HD, Q75, R8, default planar |0.2993 →0.2970|2.2066 →2.2703|2.9% slower|
| HD, Q90, R10, interleaved444 |0.3599 →0.3048|2.1768 →2.1726|0.2% lower|
| HD, Q100, R10, interleaved444 |0.5664 →0.4779|3.1817 →3.1427|1.2% lower|
| HD, Q90, R1, interleaved444 |0.4431 →0.4342|2.8753 →2.8948|0.7% slower|
| HD, Q90, R1000, interleaved444 |9.9871 →3.1795|11.8850 →5.0934|57.1% lower|
| 4K, Q90, R10, interleaved444 |0.9387 →0.7819|6.9353 →6.8896|0.7% lower|
| HD, Q90, R10, interleaved420 |0.5284 →0.2529|2.3253 →1.9940|14.2% lower|

An Nsight profile of the HD420 Q90 R10 case isolates serialization: median357.394µs →67.968µs, or5.26× speedup. This is **not** a5.26× codec speedup. Long restart intervals benefit strongly, but R1000 is not proposed as a better encoding default: it harms decode parallelism and was included to stress the serial bottleneck.

Validation produced **34/34 complete JPEG byte matches** in the image-corpus suite and **49/49** in the portable generated-fixture suite, not merely similar decoded pixels. Cases covered Q0/1/50/75/90/100;444/422/420; R0/1/2/8/10/1000; planar scans;1×1 and odd sizes; grayscale; noise; and4K. Targeted Compute Sanitizer memcheck, racecheck and synccheck reported zero errors/hazards. A portable parity runner is included in the modified source. Small/noisy correctness fixtures and actual-image performance data serve different purposes.

### Decoder table reuse

Fastvideo's observed canonical table state is4×1,120=4,480 bytes and needs no expanded-table build kernel per frame. GPUJPEG allocates four65,536-entry16-bit tables (512KiB), plus a10-bit quick table, and previously rebuilt them on every GPU decode.

The decoder patch retains the expanded-table algorithm and caches the four input table structures per decoder instance. It rebuilds on first use or when any table bytes change, including reuse of the same DHT IDs for different tables. The snapshot is marked valid only after successful kernel launch checking. Constant-memory quick tables are still refreshed for every decode, because constant memory is shared between decoder instances. The cache owns no device allocation and is released with its decoder.

Both ordinary and constant-table builds passed90 alternating decodes across six JPEGs and two decoder instances, including optimized/changed DHTs,444/420/grayscale, changed dimensions and the no-restart CPU fallback. Six separate baseline/patched CLI comparisons produced identical decoded files. Decoder memcheck passed45 alternating decodes with zero errors.

Nsight observed100 table-build launches in the baseline versus1 with caching over100 decodes of the same restart10 image, eliminating approximately11.5µs of warm GPU setup per repeat. An eight-run alternating benchmark (300 iterations/run, discard10) measured entropy medians0.534725→0.520725ms; overall codec medians were2.52535→2.564375ms. The latter is slightly slower and noisy: **no reliable end-to-end decoder speedup is claimed**. The demonstrated benefit is removal of redundant setup work, not a broad latency improvement. This is principally a repeated-image/video-stream optimization; it offers no guarantee for one-shot decoding or a stream whose Huffman tables change every frame.

## 6. What remains to transfer

| Technique | Status / engineering implication |
|---|---|
| Restart partitions | Already present in GPUJPEG; tune with encoded size and decode latency together. |
| Warp prefix bit packing and byte stuffing | Implemented, optional, measured; retain scalar fallback. |
| Avoid repeated decode table preparation | Implemented per-instance cache, keeping current lookup decoder. |
| Full token-plane decoder with separate RLE/DC scans | Documented precisely; requires layout changes, substantial intermediate storage and comparative testing before adoption. |
| GPU parallel unstuffing and marker indexing | Documented; a plausible larger decoder project, especially when CPU parsing is material. |
| Fused color conversion/DCT and IDCT/output | Observed; port requires full edge/subsampling/color-space validation. |
| No-restart lossless preprocessing | Observed and independently verified; not ported because added entropy recoding may cost more than GPUJPEG's existing CPU fallback. |
| Multi-stream frame overlap | Exposed by application interface; not measured or newly implemented here. |

No universal superiority claim is supported by this corpus. The concrete result is a reviewed, reproducible optimization experiment with preserved outputs and explicit workload limits, plus enough algorithm and runtime evidence to guide the larger decoder work.

## References and deliverables

* [REA repository](https://github.com/morluto/rea) and [Rizin CLI evidence workflow](https://github.com/morluto/rea/blob/0ba4d6f43ad610dacc861a1548be1a5c4712cd61/docs/reverse-engineering-tools.md).
* [GPUJPEG source at the investigated commit](https://github.com/CESNET/GPUJPEG/tree/78e5eee3f75e0a2a2e6c1409000ac05d679b4af8), especially `src/gpujpeg_huffman_gpu_encoder.cu`, `src/gpujpeg_huffman_gpu_decoder.cu`, `src/gpujpeg_encoder.c`, `src/gpujpeg_decoder.c`, and `src/gpujpeg_reader.c`.
* Fastvideo/NVIDIA's [2012 presentation](https://developer.download.nvidia.com/GTC/PDF/GTC2012/PresentationPDF/S0273-GTC2012-JPEG-Coding-GPU.pdf) supplies historical context for restart decoding and overlap. Current [Fastvideo product documentation](https://www.fastcompression.com/products/cuda-jpeg.htm) describes the supported pipeline and benchmark boundaries; its marketing comparison is not treated as experimental proof.
* Patch (`artifact: gpujpeg-fastjpeg-techniques.patch`), modified source archive (`artifact: GPUJPEG-source.zip`), [build and reproduction instructions](reproduction/README.md), [evidence identity](evidence/identity.json), [decoder details](decoder-analysis.md), [runtime capture details](runtime-preparation.md).
