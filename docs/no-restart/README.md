# Restart-free GPUJPEG encoding and decoding

Branch `no-restart` combines the locked encoder and experimental decoder. It is based on CESNET/GPUJPEG `78e5eee3f75e0a2a2e6c1409000ac05d679b4af8`. The encoder commit is `d94ed55e2922332958287a897bdcd83a7205a95e`; the decoder commit is `74911dfee71e4d5c278427ea04e84a63355bf082`. The decoder commit already descends from the encoder, so the combined branch needs no merge commit. The encoder lock tag remains `encoder-r0-locked-20261020`.

## Build and use

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=86 -DBUILD_OPENGL=OFF -DBUILD_SHARED_LIBS=OFF -DHUFFMAN_GPU_NO_RESTART=ON -DHUFFMAN_GPU_NO_RESTART_DECODER=ON
cmake --build build --config Release
```

Set the CUDA architecture for your GPU. On Windows use a Visual Studio x64 developer shell and a compatible CUDA toolkit. Both new paths require CUDA 11+ and SM3+; this version was validated with CUDA 13.4 on an RTX 3050, SM86. The new encoder option defaults ON; the experimental decoder option defaults OFF and must be explicitly enabled. Existing eligibility checks and fallback routes remain in force. Build directories should be separate when comparing ON/OFF configurations.

The [Windows CLI fix and regression instructions](windows-cli.md) cover raw RGB24 encoding, Unicode paths, and consistent Windows/CRT character types. The console executable can perform the full JPEG → RGB24 → JPEG round trip directly; the earlier demonstration's public-API runner workaround is no longer required.

Set `restart_interval=0` through the public API or encode with `gpujpegtool --encode --restart=0 input.ppm output.jpg`. Normal decoding selects the new path for eligible restart-free JPEGs when compiled ON. Positive restart intervals retain the existing implementations. No new public API is required. [Encoder algorithm and limits](../../HUFFMAN_NO_RESTART.md) and [decoder algorithm, correctness argument and limits](../../HUFFMAN_NO_RESTART_DECODER.md) describe the code in this branch.

## Results and evidence

On the recorded RTX 3050/Xeon E5-1650 v4 Windows machine, including transfers:

| Path | Tested natural HD/4K speedup over original GPUJPEG | Exactness |
|---|---|---|
| Encoder | 7.7–13.0x | Complete JPEG byte parity; 86-case correctness suite |
| Decoder | 3.06–6.39x | Coefficients and public pixels match for 102 RI0 fixtures |

These are warm same-image latency measurements, not universal speedups. Tiny images regress. The decoder can incur substantial serial boundary repair on poorly synchronizing streams, and remains experimental. nvJPEG had 2.3–12.7% lower encoder latency in the recorded matrix. jpeggpu was a conceptual decoder guide, not a benchmark competitor in this investigation.

- [Encoder results](archive/nvjpeg-no-restart/report.md), [measurement scope](archive/nvjpeg-no-restart/methodology.md), [nvJPEG/REA analysis](archive/nvjpeg-no-restart/analysis.md), [reproducing that analysis](archive/nvjpeg-no-restart/nvjpeg-reproduce.md), and [focused REA evidence](archive/nvjpeg-no-restart/rea-evidence-summary.json).
- [Decoder results](archive/decoder-no-restart/report.md), [raw samples and schedule](archive/decoder-no-restart/evidence/benchmark-results.json), [profile summary](archive/decoder-no-restart/evidence/profile-summary.json), and [102-case coefficient/pixel validation](archive/decoder-no-restart/evidence/validation-results.json).
- [Original GPUJPEG/Fastvideo comparison](archive/original-comparison/report.md), [initial Fastvideo observations](archive/fast-jpeg-investigation.md), [staged decoder experiment](archive/staged-decoder/report.md), and [compact-table/unstuffing experiment](archive/compact-decoder/report.md). These describe historical experiments; their rejected implementations are absent from current source.
- [Sources, attribution and boundaries](sources.md), [reproduction guide](reproduce.md), [published-document provenance](document-provenance.json) and [artifact references omitted from Git](workspace-artifact-references.json).

The archive includes the human-written reports, machine-readable benchmarks, correctness hashes, sanitizer logs and original reproduction source. It preserves the chronology and limitations of each investigation. Statements such as “active source contains the encoder only” belong to the earlier snapshot; the current branch contains both final changes. Original per-delivery manifests refer to those delivery packages. `document-provenance.json` distinguishes their original bytes from this portable documentation copy.

Generated executables, source ZIP duplicates, Git bundles, proprietary DLL/PTX/SASS, profiler databases and user-supplied source images are not Git source files. Their relevant identities and findings are documented. The final decoder text logs and metadata were extracted from the evidence ZIP into `archive/decoder-no-restart/raw-evidence/`. Git history and the encoder tag now provide source recovery. No NVIDIA/Fastvideo implementation or GPL jpeggpu code was copied into GPUJPEG.
