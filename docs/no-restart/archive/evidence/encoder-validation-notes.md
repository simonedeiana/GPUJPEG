> Archived investigation document. See the [current branch guide](../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# GPUJPEG warp serializer handoff

Implementation: `work/GPUJPEG/src/gpujpeg_huffman_gpu_encoder.cu` new
`gpujpeg_huffman_encoder_serialization_kernel_warp`; opt-in CMake
`HUFFMAN_GPU_WARP_SERIALIZATION=ON`, default OFF. Independent code uses
prefix bit positions -> shared atomic-OR packing -> prefix stuffed-byte
positions -> scalar carry of 0--7 bits. No extra device allocations/launches.
Fallback original serializer on pre-SM3; CUDA>=9 required to enable.
Build option description and rationale: `work/GPUJPEG/HUFFMAN_WARP.md`.
Portable public-output regression: `work/GPUJPEG/test/compare_huffman_serializers.py`.

Baseline was built at unmodified 78e5eee3f75e0a2a2e6c1409000ac05d679b4af8
into `work/build-baseline`; candidate `work/build-warp`, RTX3050 SM86,
CUDA13.4, VS18 MSVC14.51 Release, static, OpenGL OFF. Decoder source has
since changed independently; the encoder benchmarks used original decoder.

## Correctness executed

* `python work/port-validate.py`: 34/34 complete JPEG byte streams identical.
  Includes supplied HD/4K images, Q0/1/50/75/90/100, RI1/2/8/10/1000,
  420/422/444, interleaved/planar, synthetic1x1/19x17/noise127x65/gray61x39.
  `work/port-validation/parity.json` plus per-case logs.
* `python work/GPUJPEG/test/compare_huffman_serializers.py
  work/build-baseline/gpujpegtool.exe work/build-warp/gpujpegtool.exe`:
  49/49 exact byte streams, including Q1/75/100 x RI1/8/1000 and RI0 CPU fallback.
  `work/port-regression.log`.
* Targeted Compute Sanitizer `memcheck`, `racecheck`, `synccheck` on new kernel:
  0 errors; 0 race hazards. Each on noise127x65; Q100; RI1(memcheck),
  RI1000(racecheck), RI8/420(synccheck). Scope is the new kernel only.
  Logs `work/port-{memcheck,racecheck,synccheck}.log`.
* `git diff --check` passed.

## Warm performance

`python work/port-benchmark.py`: 3 alternating baseline/candidate process runs
per case; each110iterations; discard first10, summarize100; table is median
of the three per-process medians. Encoder's CUDA event timings; total excludes
input/output file IO but includes transfers and internal overhead. No other
agent used GPU during measurements. Display GPU WDDM, clocks uncontrolled.
Full samples: `work/port-benchmark/results.json` plus raw logs.

| Case | baseline entropy ms | warp entropy ms | baseline GPU ms | warp GPU ms | baseline total ms | warp total ms |
|---|---:|---:|---:|---:|---:|---:|
|HD Q75 RI8 planar444|.2993|.2970|.5866|.5898|2.2066|2.27025|
|HD Q90 RI10 interleaved444|.3599|.3048|.6526|.6017|2.17675|2.1726|
|HD Q100 RI10 interleaved444|.56635|.4779|.86265|.78325|3.18165|3.1427|
|HD Q90 RI1 interleaved444|.4431|.4342|.7527|.7273|2.87525|2.8948|
|HD Q90 RI1000 interleaved444|9.98705|3.1795|10.34895|3.49405|11.88495|5.0934|
|4K Q90 RI10 interleaved444|.9387|.7819|1.7341|1.5571|6.93525|6.88955|
|HD Q90 RI10 interleaved420|.5284|.2529|.78025|.4957|2.32525|1.99395|

Independent Nsight110iteration trace on HD Q90 RI10 interleaved420:
serializer median357.3935us ->67.968us (5.26x); symbol generation~93us
unchanged, preprocessor~58.8us unchanged. Full kernel CSV and SQLite:
`work/port-{baseline,warp}-kernels.csv`,
`work/port-{baseline,warp}-trace.sqlite` and `.nsys-rep`.

Keep OFF by default: gains depend heavily on entropy density and restart
geometry, and host/PCIe cost can dominate. This is a working, measured
experimental port; broad hardware coverage and production readiness are unproven.
