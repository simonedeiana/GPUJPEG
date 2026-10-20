> Archived investigation document. See the [current branch guide](../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# GPU decoding of JPEG without restart markers

The new prototype reduces transfer-inclusive HD/4K natural-image decode latency by **3.06–6.39x** on this machine. It actually performs restart-free entropy decoding on the GPU. The encoder is preserved at tag `encoder-r0-locked-20261020`, commit `d94ed55e2922332958287a897bdcd83a7205a95e`; its [lock record and artifact hashes](../encoder-locked/LOCK.json) remain unchanged. Use the complete encoder bundle (`artifact: encoder-locked-complete.bundle`) for recovery: the original shallow-history bundle failed a fresh clone and is retained unchanged for provenance. Decoder development is committed separately as `74911dfee71e4d5c278427ea04e84a63355bf082` on `decoder-r0-development`.

This is an opt-in prototype, with `HUFFMAN_GPU_NO_RESTART_DECODER=OFF` by default. Tiny and small low-entropy images regress; poorly synchronizing streams can still serialize substantial repair work. The table below includes those cases. No blanket claim that every restart-free JPEG gets faster is justified.

## What changed

Original GPUJPEG routes restart-free entropy to its CPU Huffman decoder, then uploads quantized coefficients for the existing GPU IDCT. This implementation uploads the compressed scan, removes byte stuffing on the GPU, speculatively decodes independent 1024-bit chunks, reconciles their bit/coefficient/MCU-phase boundaries hierarchically, and reconstructs output ranges and DC predictors with parallel scans. Existing IDCT and output conversion remain in use. It does not insert restart markers or build a CPU entropy index.

The conceptual guide was [jpeggpu](https://github.com/nolmoonen/jpeggpu), examined at `3ae7ac0dbc6e1b047abaa67badb6378baa7d39cb`, and [Weissenberger and Schmidt's paper](https://arxiv.org/html/2111.09219). The paper motivates speculative chunks, synchronization and prefix-scan reconstruction. The new GPUJPEG code independently implements a balanced hierarchy that propagates exact decoder states. jpeggpu is GPLv3; its implementation source was not imported. Its README and repository metadata were inspected. **jpeggpu itself was not benchmarked here.**

At each hierarchy level, both halves are already consistent. A thread re-decodes the right half from the left half's true end state until it matches a recorded end state, after which the deterministic suffix is reusable. If it never matches, it repairs the entire half. Induction anchors the final boundaries at the real scan start; there is no fixed-overlap correctness assumption. The exact state and count rules, coefficient placement, DC ordering, memory costs and eligibility checks are documented in [implementation.md](implementation.md).

An initial implementation hit a real optimized-flat-image pathology: HD took 24.91 ms against 3.47 ms for the old decoder. An exact zero-region shortcut checks the actual Huffman code lengths and zero bits plus a guard, then computes repeated DC0/EOB transitions arithmetically. This removed the observed pathology; final HD optimized-flat latency is 2.879 ms. It does not establish fast convergence for all codebooks or images.

## Measured performance, including transfers

NVIDIA RTX 3050 8GB, SM86, driver 617.42, PCIe Gen3 x8 negotiated; Xeon E5-1650 v4; Windows 10 Pro for Workstations 19045/WDDM; CUDA 13.4.59; MSVC 14.51; Release, SM86 static builds. The same benchmark object was linked to either the locked library with its original decoder or the candidate library. Both had the locked encoder enabled, which is irrelevant to these decode calls.

Each process decodes one already memory-resident JPEG 110 times, discards 10 warmups, and reports the median of the remaining 100 wall-clock samples. Five rounds randomize both image order and codec order with seed 20261021. The table reports the median of those five medians: **150 processes and 15,000 retained call samples**. Output is complete host pixels. The timer starts before the public decode call and stops after explicit stream synchronization, including JPEG parsing, every H2D transfer, entropy decoding, IDCT, conversion and D2H pixels. Initialization and disk IO are outside the timer. No profiler ran during this benchmark. All ten output hashes per case match.

| Input | Locked decoder ms | New decoder ms | Speedup |
|---|---:|---:|---:|
| hd-rgb-q90-444-interleaved | 19.336 | 3.685 | 5.25x |
| hd-rgb-q90-422-interleaved | 15.923 | 3.687 | 4.32x |
| hd-rgb-q90-420-interleaved | 14.069 | 4.568 | 3.08x |
| hd-gray-q90-gray-interleaved | 10.934 | 2.199 | 4.97x |
| 4k-rgb-q90-444-interleaved | 62.295 | 11.082 | 5.62x |
| 4k-rgb-q90-422-interleaved | 51.037 | 9.974 | 5.12x |
| 4k-rgb-q90-420-interleaved | 44.611 | 10.633 | 4.20x |
| 4k-gray-q90-gray-interleaved | 33.897 | 5.308 | 6.39x |
| hd-rgb-optimized-2 | 13.904 | 4.546 | 3.06x |
| 4k-rgb-optimized-2 | 43.751 | 10.671 | 4.10x |
| flat-512-0 | 0.715 | 1.210 | 0.59x |
| flat-512-1 | 0.704 | 1.848 | 0.38x |
| flat-1920-0 | 3.422 | 2.574 | 1.33x |
| flat-1920-1 | 3.370 | 2.879 | 1.17x |
| tiny-gray-1x1 | 0.118 | 0.252 | 0.47x |


HD means 1920x1080; 4K means 3840x2160. Natural inputs marked q90 use the original fixture encoder's standard tables. Optimized-2 inputs use Pillow/libjpeg optimized tables and 420 sampling. Flat-0/1 designate standard/optimized tables; the small flats are 512x512 and large flats 1920x1080. A speedup below 1 is a regression. The 1x1 case takes approximately twice as long, and the 512x512 optimized flat takes approximately 2.6 times as long. The experimental route currently has no automatic small-image tuning threshold.

These are warm, repeated same-image latency measurements on one desktop GPU, with five process-level rounds. They do not establish cold-start, batched, concurrent throughput or performance on other GPUs. [CSV](benchmark.csv) and [round ranges](evidence/benchmark-summary.json) provide the full distribution summary; [individual samples](evidence/benchmark-results.json) and [configuration/hashes](evidence/benchmark-configuration.json) preserve provenance. This is a comparison against the locked GPUJPEG decoder; it is not a new head-to-head result against Fastvideo, nvJPEG or jpeggpu.

## Evidence that entropy is decoded on the GPU

Separate Nsight captures of 30 HD420 and 30 4K420 calls contain `dr0_unstuff`, `dr0_speculate`, `dr0_reconcile`, scan, output and DC-restore kernels. Per frame the H2D payload is exactly the stored compressed entropy: 531,811 bytes for HD and 1,545,805 bytes for 4K. There is no per-frame quantized-coefficient upload. Initial block-list metadata is uploaded once. D2H contains the full 6,220,800/24,883,200 host pixel buffers and a four-byte error status per frame.

The profile identifies the next bottleneck: boundary reconciliation averages about 2.37 ms for HD420 and 2.69 ms for 4K420, while 4K host pixel transfer averages 3.80 ms. Output reconstruction is another substantial 4K cost, about 1.84 ms. These diagnostic stage averages include all 30 profiled frames and profiling overhead; they are not the unprofiled table's latency components. See [profile summary](evidence/profile-summary.json) and raw captures in raw evidence (`artifact: raw-evidence.zip`).

## Correctness and compatibility

All **102 restart-free JPEG fixtures** match unmodified upstream GPUJPEG byte for byte in both the final int16 quantized coefficient stream and public decoded pixels. Tests cover HD/4K natural inputs, grayscale, 444/422/420, interleaved and separate scans, odd/tiny dimensions, qualities 0/1/75/90/100, noise, independent optimized Huffman tables, flat colors and two custom all-zero-code fixtures. Independent marker parsing confirmed RI0 and no restart markers for every fixture. The two custom fixtures also match independently known all-zero coefficients, rather than only agreeing with a reference implementation.

The dump harness reuses each decoder across two passes through each batch. Two additional simultaneous decoders on independent streams pass six mixed inputs each, including a restart-coded image, changing image sizes, grayscale and table changes. Decoder-disabled builds and a valid mismatched DC0/AC1 table fallback preserve output parity. These checks cover instance reuse and the tested concurrency scenario, not arbitrary application thread sharing.

Compute Sanitizer reports zero errors on memcheck for the final small grayscale partially-pinned transfer regression and the custom zero-code 420 fixture; racecheck reports zero hazards on a Q100 noise 420 separate-scan fixture, and synccheck reports zero errors on the final optimized flat-color HD case. Earlier intermediate memcheck/race/sync logs are also retained and labeled separately. The sanitizer runs cover the full decoder pipeline. [Validation summaries and hashes](evidence/validation-summary.json), [per-fixture results](evidence/validation-results.json), [concurrency](evidence/interop-summary.json) and [fallback](evidence/fallback-summary.json) preserve the actual scope.

The route currently requires supported sequential scan layouts, restart interval zero, matching DC/AC table indices in 0 or 1, at most 16 MCU block phases, at most 2,097,151 coded blocks and at most 16 MiB stored entropy per scan. Ineligible images retain GPUJPEG's previous route. Constant-table decoder builds also retain the old route. Extra reusable per-instance memory is approximately `2B + 24 + 32C + 8U + 16N + W` bytes, with byte capacity B, C=ceil(B/128), U=ceil(B/256), block capacity N, and recursive-scan workspace W. Full details are in the implementation note. This work has not been fuzz-hardened or established as a replacement malformed-JPEG validator.

## Deliverables

Full source (`artifact: GPUJPEG-decoder-source.zip`), incremental patch against the locked encoder (`artifact: decoder-no-restart.patch`), recoverable Git bundle (`artifact: decoder-development.bundle`), experimental Windows SM86 tool (`artifact: gpujpegtool-experimental-sm86.exe`), 102 test fixtures (`artifact: fixtures.zip`), raw evidence (`artifact: raw-evidence.zip`), and [build/reproduction instructions](README.md). [manifest.json](manifest.json) hashes the package. The earlier encoder artifacts and its implementation files are unchanged.
