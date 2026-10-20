> Archived investigation document. See the [current branch guide](../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# GPUJPEG decoder table cache validation

Change: `src/gpujpeg_huffman_gpu_decoder.cu` now retains a per-instance CPU snapshot of all four source Huffman tables. It launches the262144-entry expanded-table builder only when the tables differ or the decoder is new. The optional constant-memory quick LUT is still refreshed on every decode, because it is shared between instances. Table contents, table IDs, component mapping and codeword decode algorithms are unchanged. Cache allocation failure returnsNULL with cleanup. Snapshot validity is set after successful kernel-launch checking; CUDA stream ordering guarantees the setup completes before decode in the same stream.

Build: Visual Studio18 Community /MSVC14.51, CMake Ninja Release, CUDA13.4, SM86, static GPUJPEG, OpenGL off. GPUJPEG base78e5eee3f75e0a2a2e6c1409000ac05d679b4af8. RTX30508GiB, Windows WDDM, driver617.42. Encoder experimental toggle disabled for decoder tests.

Executed validations:

- Fresh-reference versus same-instance and two-instance alternating decodes:90successful cached decodes across6inputs in normal build, and90in `HUFFMAN_GPU_CONST_TABLES=ON` build.
- Generated default-DHT444 and optimized-DHT444/420/grayscale JPEGs with restart7, odd dimensions127x95/129x97/79x61, plus original FastvideoRST10profile and GPUJPEGr0CPUfallback JPEGs. The DHTs change under the same IDs. Sequences include duplicate image hits, A/B/A changes, grayscale/color and dimensions changes, and a second instance changing shared constant tables between first-instance cache hits.
- Six independent baseline CLI decodes versus patched CLI decodes produce byte-identical PNM files; hashes in `cache-validation.json`.
- Compute Sanitizer memcheck:45cached decodes across3generated DHT inputs, zeroerrors.
- `git diff --check` passes.

Nsight100-iteration same-file decoding of1920x1080interleaved444RST10:

| Measurement | Baseline | Cached |
|---|---:|---:|
| Expanded-table kernel launches |100|1|
| Total expanded-table kernel time |1.144136ms|0.011840ms|
| Warm baseline table setup median |11.520us|omitted on99hits|
| PPM output SHA256 |D03A1357AFA85F91F9942EC887552A71716BD2BE8894073ED6456BF4F16770D2|same|

Other CUDA kernel medians were essentially unchanged in this100-iteration profile. The extra host snapshot pointer keeps the large cache out of kernel-by-value parameters.

Unprofiled ABBA test:8processes,300iterations/process, discard first10; order baseline,cached,cached,baseline,cached,baseline,baseline,cached. Median of the four per-process warm medians:

| Reported interval | Baseline ms | Cached ms |
|---|---:|---:|
| Huffman stage |0.534725|0.520725|
| Total GPU processing |0.787775|0.780575|
| Full decode including transfers and reader |2.525350|2.564375|

This shows approximately14us less in the reported Huffman stage, consistent with eliminating an11.5us kernel plus launch overhead. It does **not** establish an end-to-end speedup: wall performance varies and cached median is1.5%slower in this sample. The reliable claim is removed repeated work and identical decoded output, with a small local GPU-stage improvement. Do not extrapolate to unique-image changing-DHT workloads or other GPUs.

Reproduce: run `make-test-images.py` (Pillow13, installed locally underwork/test-python), `build-cache.cmd`, then `cache-regression.exe` with the sixJPEG paths. Repeat `build-cache-const.cmd` and `cache-regression-const.exe` for constant-LUT variant. `benchmark-cache.py` generates the comparison/ABBAlogs and `cache-validation.json`. Nsight commands are retained in shell logs and traces `baseline.nsys-rep`, `cached.nsys-rep`; CSV summaries are `*-stats_cuda_gpu_kern_sum.csv`. The parent should package portable versions of these scripts or document corresponding paths when delivering outside this workspace.
