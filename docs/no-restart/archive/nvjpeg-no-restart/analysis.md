> Archived investigation document. See the [current branch guide](../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# nvJPEG GPU encoding with restart interval zero

This investigation observes the installed CUDA 13.4 nvJPEG Windows library and
builds an independent public-API harness. Proprietary PTX/SASS stays in scratch;
it is not code to copy into GPUJPEG. The implementation guidance below describes
algorithms, with unknown details distinguished from observations.

## Identity and REA evidence

DLL: `C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v13.4/bin/x64/nvjpeg64_13.dll`

SHA256: `957bb73818e62883ac4bfc0aa15204bba5e341a8f7964a11b0289742df4e0da2`

| REA record | Evidence ID | Established observations |
|---|---|---|
| `rea-exports.json` |`ev_eeb83d403c536eab54e949a18452cf4650fbb249648c97b1391865958ab1f9b9`|Actual exports and addresses of encoding, retrieval, backend selection and restart setter APIs|
| `rea-info-strings.json` |`ev_726a024647055bfcf4d42f30087e9493bd92ed53e406080253a7aedddc950d87`|PE64 identity and embedded encoder kernel names, including DC/AC coding, scans, assembly and stuffing|
| `rea-encode-api.json` |`ev_9d6bfd3ba7923d74877445009fa7b2216f0a00f7fa72d77c8ab20be69bc7910a`|Native disassembly of encoding entry, backend constructor and restart setter|

Preferred PE image base is 0x180000000. Exports:
`nvjpegEncodeImage` 0x18001eb60, `nvjpegEncodeRetrieveBitstream` 0x18001f0c0,
`nvjpegEncodeYUV` 0x18001f480, restart setter 0x180020370, explicit-backend
state creation 0x180020a20. These are static preferred addresses, not ASLR addresses.
REA 6.7.0 was invoked through its existing cached CLI and caller-supplied Rizin;
no REA configuration was repaired or changed.

CUDA's cuobjdump recovered embedded compute 121 PTX plus actual SM 86 machine code.
The PTX supplies the semantic reconstruction below. SM 86 SASS independently
confirms atomic OR assembly (`RED.E.OR.STRONG.GPU`). Nsight traces on the RTX 3050
confirm the exact named kernels execute. This triangulates the mechanism but
does not recover original NVIDIA source, all host object fields, or every
architecture-specific scheduling detail.

## How restart-free encoding remains parallel

The encoder can know every block's DC difference before entropy coding: quantized
DC coefficients already exist. Computing `DC[b] - DC[previous block of the same
component in JPEG scan order]` is a neighbor read, not a dependency on an encoded
predecessor. The first component block predicts zero. Variable output length is
handled by prefix sums and a later merge, rather than restart markers.

Observed standard-Huffman baseline pipeline, in runtime order:

1. RGB conversion/subsampling, then forward DCT and quantization per component.
2. Per component, AC coding then DC coding into private block bit fragments and
   integer block lengths. `ACKernel<8,WARP_COM=0>` launches 32x8 threads, assigning
   a warp to each block. Each lane loads up to two coefficients; ballots/CLZ
   determine zero runs, warp scans assign shared RLE slots, then table lookups
   generate Huffman bits. `DCKernel` uses one thread per block and shared current
   DCs plus an explicit predecessor load at each CTA boundary. It subtracts
   adjacent scan-order coefficients, calculates category/amplitude and appends
   coding to that block's private scratch fragment. This private scratch order
   should not be mistaken for final DC-before-AC JPEG bit order.
3. `OutputOffsetKernel<ScanOrderInterleaved>` permutes component block lengths
   into final interleaved JPEG block order. It performs 64-bit inclusive scans
   using warp shuffles, shared warp totals and CTA totals. `SumBlocksKernel` and
   `UpdateBlocksKernel` complete the global scan. Thus each block's start is its
   inclusive end minus its own bit length. DC prediction order and final
   interleaving order are both explicitly represented.
4. `BlockAssembleKernel` assigns one thread to each block, reads its private
   words from high ordinal to low ordinal, shifts them into final stream bit
   positions and byte-reverses words. Multiple blocks may share an aligned
   output word, so contributions use atomic OR into a zeroed destination.
   Only the last block adds one bits to the final byte when total bit length is
   not byte aligned. No restart marker or predictor reset is introduced.
5. A second scan allocates space for byte stuffing. `BlockLengthKernel` handles
   four unstuffed bytes per thread and counts valid source bytes plus one extra
   byte for each FF. The same SumBlocks/UpdateBlocks hierarchy produces global
   inclusive stuffed-byte offsets. `ByteStuffingKernel` obtains the preceding
   group's end offset and writes its four bytes to a disjoint range, inserting
   a zero after every FF. The smaller downloaded entropy-sized payload compared
   with the full JPEG is consistent with host-side container/header assembly;
   the exact destination-pointer mapping was not captured.

For HD444, 97,200 coefficient blocks produce 380 offset and assembly CTAs:
offset launch 32x8 threads and assembly 256x1. The stuffing scan has 669 CTAs for
the observed unstuffed entropy. The runtime frame shows a 0-fill of 684,288 bytes
immediately before assembly; the assembly follows it on the same stream.
An earlier 0-fill of 12,441,600 bytes clears coding scratch. These sizes are
observations for this input and settings, not universal allocation rules.

## Independent implementation guidance

### Private binary-analysis anchors

Line numbers below refer to the exact local UTF-8 cuobjdump output, indexed and
hashed in `evidence-index.json`. They are reproducible coordinates into private
scratch, rather than proprietary code to redistribute.

| Mechanism | Private PTX line anchors |
|---|---|
| DC per-block current coefficient and predecessor-at-CTA-edge load |`nvjpeg.ptx`55263,55268–55291|
| Actual current-minus-predecessor DC operation |55298|
| AC warp ballots, preceding nonzero and shared warp synchronization |55561,55567,55636,55638,55718|
| Scan-order length read and 64-bit block-inclusive scan |58067,58071–58196|
| Per-block inclusive-end and CTA-total stores |58208,58215|
| Global scan top and uniform-add stages |57547–57765|
| Fragment start=end-length |59134|
| Cross-word atomic-OR assembly contributions |59173,59224,59275,59326,59375,59406,59426|
| Last-block one-bit byte padding |59342–59396|
| Four-byte valid count plusFF count before stuffing scan |57371–57394|
| Stuffing-group start from previous scanned endpoint |54609–54617|
| Disjoint byte output and inserted00 afterFF |54634–54668|

SM 86 corroboration is in `encoding-sm86.sass`: BlockAssemble begins at line140;
its function-relative instruction offset0x10a0 (line675) is an atomic OR
reduction, with additional OR contributions at0x13c0/0x16e0/0x1a00 and final
tail contributions0x1ce0/0x1e70/0x1f40. DCKernel begins at line1250 and
ByteStuffingKernel at line1752. Full PTX/SASS must remain private; the normalized
instruction facts above and tool/source hashes are sufficient for the report.

A port can use its own block layout, Huffman encoder and CUB/custom scans:
compute cross-block DC differences from quantized coefficients, independently
encode each block, prefix-scan bit lengths in JPEG order, merge bits at precise
positions, pad the stream once, then scan FF counts and stuff bytes. A simpler
private fragment order of DC then AC is sufficient; NVIDIA's scratch layout,
unrolling, object layouts and instruction sequences are unnecessary.

Zero the OR target, bound every fragment, map predecessor DCs by component and
MCU order, avoid byte padding between blocks, mask unused fragment bits, pad
with one bits only once at the scan tail, and count stuffing only for valid
source bytes. A word-owner gather merge is another independent design that
could avoid atomic OR. Its speed relative to atomic scatter is unmeasured here.

The exact complete `ScanOrder` ABI, all allocator policies, maximum fragment
capacity rules, unsupported encoding modes and optimized-Huffman policy are
not established by these four standard-table baseline traces. Optimized-table
generator kernels are embedded but not executed in these profiles. This work
does not establish behavior for progressive or hardware encoding.

## Harness and transfer-inclusive timing

### Profiled stage and transfer medians

Each row uses 20 warm frames from a 30-frame trace. All times below are in
milliseconds. Stages sum launches within each frame before taking the median.
`bit scan` includes OutputOffset plus its SumBlocks/UpdateBlocks;
`stuff scan` includes BlockLength plus its second SumBlocks/UpdateBlocks.

| Input | AC | DC | Bit scan | Assembly | Stuff scan | Stuff write | Complete entropy kernels |
|---|---:|---:|---:|---:|---:|---:|---:|
| HD444 |0.261536|0.035888|0.015088|0.028768|0.022848|0.012096|0.375921|
| HD420 |0.152081|0.020448|0.009792|0.019040|0.012784|0.008880|0.222865|
| 4K444 |0.879041|0.104769|0.066785|0.079745|0.072656|0.039488|1.242355|
| 4K420 |0.491778|0.066928|0.034352|0.050401|0.057216|0.030912|0.730611|

Complete entropy-kernel medians are calculated separately per frame; they are
not sums of the displayed stage medians. RGB conversion and DCT medians are
also retained in `profiles/stage-summary.json` rather than attributed to
entropy work. All-kernel medians are 0.549378/0.326065/1.896358/1.105763ms in
the same row order.

| Input | Raw H2D bytes | Raw H2D active ms | All D2H bytes | All D2H active ms | Upload→last download diagnostic ms |
|---|---:|---:|---:|---:|---:|
| HD444 |6,220,800|1.414068|685,570|0.107984|2.532471|
| HD420 |6,220,800|1.404516|524,014|0.083872|2.271462|
| 4K444 |24,883,200|6.159266|2,000,348|0.310641|9.118555|
| 4K420 |24,883,200|6.169891|1,538,599|0.240432|8.221593|

Every warm frame additionally uploads 12,288 bytes of smaller data; all-H2D
byte and duration medians are retained in the stage JSON. The D2H totals include
two 8-byte metadata downloads and one entropy-sized download. Active DMA times
are activity durations, not host API wall times; the upload→download diagnostic
also includes device/host gaps inside that window. The independent, unprofiled
host timer includes the complete API work and is reported separately below.
Subtracting these medians from a host median would not yield a measured CPU
overhead. Per-frame stage/transfer observations remain available for inspection.

`nvjpeg_encode.cpp` explicitly selects `NVJPEG_ENC_BACKEND_GPU`, baseline DCT,
restart interval 0, quality 90 and optimized-Huffman 0 by default. RGB input is
packed RGBRGB... using `NVJPEG_INPUT_RGBI`; gray uses one channel and
`nvjpegEncodeYUV(...NVJPEG_CSS_GRAY...)`. Inputs/outputs are pageable by default.
Allocation, context creation, parameter setup and file I/O are outside timers.

Each `primary_ms` sample brackets, with `std::chrono::steady_clock`:
raw input H2D → nvJPEG encode → retrieve compressed bitstream into host buffer →
`cudaStreamSynchronize`. The final host JPEG is written afterward. The output
is audited for SOI/SOS/EOI, no RST, and absent or zero DRI. Output buffer capacity
comes from `nvjpegEncodeGetBufferSize`; there is no extra untimed size query per
frame. Internal warm allocator/setup work remains in the early samples and is
removed by the stated warmup. The optional pinned input mode is not used here.

RGB444 and gray 96x64 smokes both passed. Four natural cases then used 40 frames
with 10 warmups, followed by 30-frame Nsight traces with 10 warmups. Preliminary
unbalanced host medians are 2.76570ms HD444, 2.50220ms HD420, 9.40190ms 4K444,
8.31155ms 4K420. These are diagnostics; use the separately balanced three-codec
benchmark for claims of relative performance.

All 120 profiled raw uploads and output retrievals were observed. Every frame
executes the encoding, bit-offset, assembly, stuffing-scan and stuffing kernels.
HD444 uploads 6,220,800 raw bytes. One warm frame downloads 8+8 bytes of metadata
and 685,554 entropy bytes, while the returned complete JPEG has 686,179 bytes:
the complete JPEG has a 623-byte header through SOS and a 2-byte EOI beyond the
entropy-sized payload. Host-side container assembly is inferred from these
sizes, not proven by capture of the D2H destination pointer. Requiring a single
D2H equal to complete JPEG size would be wrong. Host synchronization and output-file validation establish
complete output. Transfer-window diagnostics exclude CPU work before upload;
they are never substituted for the harness's complete host timer.

Concrete HD444 frame10 trace proof: entropy D2H correlation937 transfers685,554
bytes at826575189–826687829ns; its `cudaMemcpyAsync_v3020` host interval is
826529059–826745290ns, already returning after completion in this pageable case.
Stream synchronization correlation938 follows at826747355–826754253ns before
the host timer stops. No `-discard`, untimed retrieval or GPU-only substitute is
used.

Per-frame downloads, kernel names, launch resources and durations are retained
in `profiles/profiles.json`, along with the unprofiled timing observations.
`profiles/integrity.json` verifies input, EXE, DLL, header and import-library
hashes before and after profiling. Full proprietary PTX/SASS is investigative
scratch and should not be redistributed with the independent implementation.

## Frozen independent GPUJPEG candidate: paired diagnostics

The independently written GPUJPEG implementation was then profiled on the SAME
raw HD/4K444/420 inputs and settings, again 30 frames with 10 warmups. Candidate
harness SHA256 is
`f11692097089eaba117fb7ee531eea5c954418461bc4233c746b71e3f7149692`;
GPUJPEG static library SHA256 is
`1e440031b21c010450b55b00b2704bd077113b7352a34c92bf7fa680758dfea8`.
All source, library and harness hashes matched the frozen identity manifest
before and after these traces. All 120 candidate raw uploads and output
retrievals were present, and complete output JPEGs passed the RI0 audit.
Every warm frame executed warp block coding, bit packing, byte-count and
byte-write kernels. No fallback path silently replaced the experiment.

The candidate integrates AC and DC coding in one warp kernel, so its closest
coding-stage comparison is the sum of vendor AC+DC durations PER FRAME.
The candidate's assembly column includes explicit clear and pad kernels;
nvJPEG's table above reports its assembly kernel alone, while its separate
zero-initialization activities are retained in the paired JSON.

| Input | Candidate block coding | Candidate bit scan | Candidate clear+pack+pad | Candidate stuff scan | Candidate stuff write | Candidate entropy total | nvJPEG entropy total |
|---|---:|---:|---:|---:|---:|---:|---:|
| HD444 |0.488146|0.006736|0.141521|0.020160|0.020832|0.677443|0.375921|
| HD420 |0.249329|0.005152|0.070336|0.016416|0.014336|0.355425|0.222865|
| 4K444 |1.920105|0.015968|0.541314|0.053680|0.058720|2.601067|1.242355|
| 4K420 |0.969844|0.009536|0.283905|0.040336|0.046160|1.349878|0.730611|

Units are milliseconds. Entropy totals are separate per-frame summed-duration
medians. Vendor zero-initialization active medians are 0.062464/0.061952/
0.243760/0.241825ms in row order, in addition to its kernel-only totals. Private
scratch coding and assembly account for most of the remaining GPU gap in these
observations; global scans are comparatively small. This describes measured
stages, without asserting an unmeasured microarchitectural cause.

Kernel-only entropy duration is about 1.6–2.1× nvJPEG here. That comparison is
asymmetric about initialization: candidate clear/pad work is a named kernel,
whereas vendor private/output clearing appears as CUDA memset activity. Both
sets of memset activities are therefore reported and added PER FRAME below.
Candidate also has small output-counter/tile-count memsets; these are included.

| Input | Candidate entropy kernels + memsets | nvJPEG entropy kernels + memsets | Candidate all kernels | Candidate memset | Candidate all kernels + memsets | nvJPEG all kernels | nvJPEG memset | nvJPEG all kernels + memsets |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| HD444 |0.680035|0.438545|0.839476|0.002624|0.842100|0.549378|0.062464|0.611826|
| HD420 |0.357569|0.285073|0.462674|0.002160|0.464930|0.326065|0.061952|0.388034|
| 4K444 |2.607723|1.486437|3.254493|0.005696|3.260382|1.896358|0.243760|2.139926|
| 4K420 |1.353350|0.972212|1.799064|0.003296|1.802456|1.105763|0.241825|1.347236|

After including these initialization activities, the entropy ratio is about
1.25–1.75×. All values remain device-activity duration sums, not full host
latency or elapsed GPU timeline windows; DMA transfers and inter-operation
gaps are separate. The JSON retains individual memset operations and combined
per-frame sums, so no total above is obtained merely by adding medians.

Candidate raw H2D active medians are 1.599911/1.509671/6.236748/6.217947ms;
all D2H active medians are 0.108561/0.085264/0.309266/0.240017ms. Those traces
contain only the raw image H2D per warm frame, compared with the vendor's
additional 12,288 small H2D bytes. These independent profiled observations
include natural timing variability; they must not replace the randomized,
unprofiled whole-call benchmark. In particular, the 4K pageable raw upload
already consumes about 6.2ms and substantially limits transfer-inclusive latency.

`candidate-profiles/profiles.json` retains precise per-frame launches and copies;
`paired-stage-comparison.json` adds categorized durations, transfers and vendor
memset activities. `compare_stages.py` reproduces the paired summary without
executing GPU work. GPUJPEG's complete JPEG byte parity and source quality are
validated by the separate correctness/benchmark suite.
