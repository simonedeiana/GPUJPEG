> Archived investigation document. See the [current branch guide](../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# Implementation review: restart-free GPU encoder

Measured/tested identities: final-implementation-identities.json in this directory.
Final delivery identities after EOF-only cleanup: final-delivery-identities.json.
Active source is work/GPUJPEG; final build is work/build-encode-r0.
No GPU work was performed for this review.

## Scope and anchors

- CMakeLists.txt:13,177: HUFFMAN_GPU_NO_RESTART defaults ON, enabled for CUDA11+;
  only the Huffman encoder CUDA translation unit requests C++17 and MSVC's
  conforming preprocessor. COMPILED_OPTIONS remains a space-separated string
  via set(), matching upstream.
- src/gpujpeg_encoder.c:498: selects CPU fallback only for unsupported RI0
  configurations; :541 chooses the new GPU function for supported RI0.
  :579,:591,:593,:605,:623,:625 skip segment-info hooks and final-RST removal
  only for RI0. Positive restart intervals keep their original GPU path.
- src/gpujpeg_huffman_gpu_encoder.cu:34 imports CUB conditionally; :66 adds only
  opaque-instance scratch fields; :1055 frees them; :1067 includes the helper.
- src/gpujpeg_huffman_gpu_encoder.h:42 adds internal supported/encode functions.
- src/gpujpeg_huffman_no_restart.cuh:15 warp block coding; :110 global packing;
  :129 final padding; :142 tiled stuffing; :178 scan allocation; :188 reserve;
  :213 eligibility; :229 stream-ordered orchestration.
- HUFFMAN_NO_RESTART.md documents the algorithm, nvJPEG correspondence,
  differences, memory, fallback and measurement scope.

After git add -N, packaging exposed one redundant EOF blank line in each new
source/doc file. Both were removed after the benchmark; final git diff --check
passes including both new files. The EOF proof and rebuilt-code comparison
are recorded below.
Tracked changes are exactly CMake plus the three
encoder files above; new files are the helper and documentation. Decoder is
unchanged against upstream. Searches found none of the older staged/compact/
unstuff decoder options or prior warp-serialization option in active source.
The prior experiments remain archived separately; they are not part of this
new RI0 patch.

## DC predecessor proof

Let B be total coefficient blocks per interleaved MCU, Cc the number of
blocks for component c, Ac the component's first block within an MCU, and j
the scan-local block index. Existing block_list already follows JPEG MCU,
component, vertical-block, horizontal-block order. The within-component
index is j%B-Ac.

- If within>0, predecessor j-1 is the prior block of this component.
- If within=0 after MCU0, predecessor j-B+Cc-1 is the last block of this
  component in the preceding MCU.
- In MCU0, the first block of every component has no predecessor and uses0.

For420: B=6,CY=4,ACb=4,ACr=5. Y0 of MCUk predicts the preceding MCU's Y3;
Y1/Y2/Y3 predict their immediately preceding Y. Cb and Cr each predict the
same component one MCU earlier. For422: B=4,CY=2,ACb=2,ACr=3 and the same
formula gives prior Y1, prior Cb and prior Cr. For444: B=3,Cc=1, so each
component's predecessor is j-3. For planar scans and grayscale, B=1 and
j-1 is used only when j>0; each scan starts at local j=0 and resets to0.

The predictor is the predecessor's raw quantized DC coefficient, so every
block can independently compute its difference. No prefix scan or serial
reconstruction of DC values is required in an encoder.

## Bits, stuffing and bounds

A warp loads two zigzag coefficients per lane. Existing GPUJPEG paired-RLE
semantics identify each16thzero/ZRL and final EOB. Each lane gets at most two
27-bit packed codes. A warp sum allocates its lane's bit region; shared
atomic OR merges disjoint fields in a cleared64-word buffer, with __syncwarp
before and after writes. Invalid partial-tail warps return uniformly.

CUB exclusive-sums per-block lengths. Cleared raw words receive shifted
private words through global atomic OR. An extra guard word covers a carry
from the final private word. Stream ordering completes clear before pack,
pack before padding and padding before byte tests. Final one-bit padding is
p=(-totalBits)&7; no byte is added for p=0. Since32 is divisible by8 this
padding never crosses a word boundary.

Byte extraction uses MSB-first shifts from numeric words; it does not rely
on host/device byte endianness. Only ceil(totalBits/8) valid bytes contribute
FF flags. Each256-byte tile uses warp ballots and shared warp totals; its
output size is validBytes+numberOfFF. CUB scans tile counts. The scatter
writes byte i to tileBase+i+precedingFFCount and writes00 at the next byte
only when the source byte isFF. These destinations are disjoint. Each scan
records a compact output base; no RST is ever generated.

For8-bit baseline JPEG, maximum unstuffed block size is27+63*26=1665bits
(category11DC/category10AC, maximum16-bit prefix), bounded conservatively
by2048bits/256bytes. ZRL replaces at least16zero coefficients and does not
increase that dense maximum. Thus tiles<=N and stuffed output<=512N.
Eligibility verifies existing compressed allocation>=512*imageBlockCount.
The uint32 scan bound N<=floor(UINT32_MAX/2048)=2,097,151 keeps bit/byte
prefixes and padding arithmetic within range.

Extra persistent device allocation for capacity N=max scan blocks seen is
272N+4+CUBTemp bytes: raw256N+4, four uint32 arrays16N, plus exact CUB
DeviceScan query. Existing d_temp_huffman stores private64-word streams.
Different scans reuse capacity; all scratch belongs to this encoder's opaque
GPU state. Standard global encoder LUTs are inherited upstream state, not new
mutable per-image scratch.

Growth frees old scratch, clears each pointer and capacity, then allocates.
Partial allocation/query failure returns an error; nonnull pointers remain
owned and are freed on destroy or next reserve. Kernels and copies use the
coder stream. Final four-byte output-size D2H explicitly synchronizes before
the caller consumes size. A failed GPU frame is not retried on the CPU.
CPU fallback is selected before entropy work for disabled option, CUDA<11,
SM<3, >2,097,151 image blocks or insufficient existing compressed capacity.
This is an encoder-output bound, not a malformed-JPEG decoder claim.

## Sanitizers and exact log identities

All tests below use noise-rgb127x65, Q100,420,3 public encode calls,1warmup.
Final warp lib SHA256:
1e440031b21c010450b55b00b2704bd077113b7352a34c92bf7fa680758dfea8
Final harness SHA256:
f11692097089eaba117fb7ee531eea5c954418461bc4233c746b71e3f7149692

| Log | Final variant / setting | Result | SHA256 |
|---|---|---|---|
| memcheck-final.log | warp, interleaved, whole encoder |0errors|83e1b044fa6683c8259f44e13f15f8bf6b82ed0e5b8a5663b01de3dabffb5d84|
| racecheck-final-exclude-dct.log | warp, planar; excludes only kernel substring gpujpeg_dct, includes all new and CUB kernels |0hazards,0errors,0warnings|68eeabea1d6ef601fa387761cbe5d2941db9ce3ad17914d5e33d8544880ade6b|
| synccheck-final.log | warp, planar, whole encoder |0errors|ff84ad581f3e2c42ee4beb2e5a2a45b9d14f62885b7c37dec7340bf9cbf90e17|
| racecheck-baseline.log | original baseline, planar, whole encoder |72displayedDCTwarnings,0errors|3548fa36199b977ce590dcfe41ceea32608e8f122cf1638d45b0e3be71ba0b73|
| racecheck-r0.log | first scalar, planar, whole encoder |72displayedDCTwarnings,0errors|8efd82a7a329f4b960a64e46bd84b9ea400977a1c694ae382af6e47e9f7d24f3|
| racecheck-r0-exclude-dct.log | first scalar, planar, excluding only DCT |0hazards,0errors,0warnings|f7318297e9e6b05366c2fa73da14dff94a2e4faa8119d97251a7effab5f9bd01|

The whole baseline and first-scalar race logs name only the unchanged
gpujpeg_dct_gpu_kernel<4>. Do not claim the entire encoder is racecheck-clean:
the focused final test deliberately excluded those established upstream DCT
warnings. Memcheck and synccheck did not filter kernels.

## Preliminary scalar versus warp ablation

First scalar lib/helper are saved in work/encode-r0-first; isolated initial
warp build is work/build-encode-r0-warp. All three smoke shapes below produced
exact fullJPEG parity. Five warm observations after3warmups, ordinary host
input/output, Q90 interleaved, one process/version/shape:

| Shape | Scalar median ms | Warp median ms |
|---|---:|---:|
| HD420 |2.6773|2.4236|
| HD444 |3.6431|2.8096|
|4K444|13.4117|10.5284|

These are explicitly unbalanced, short engineering smokes, not final
benchmark results. Their source JSONs are scalar-smoke420.json,
warp-smoke420.json,scalar-smoke444.json,warp-smoke444.json,
scalar-smoke4k444.json,warp-smoke4k444.json. Final balanced benchmark should
supersede these figures. The isolated warp retained an unused scalar emitter;
the final main source removed that unused helper and was rebuilt before the
final sanitizer checks. No claim that old-library sanitizer hashes prove
the rebuilt final binary is made.

## Final EOF-only cleanup

After all correctness, sanitizers and balanced measurements completed, exactly
one newline byte was removed from each of the helper and its documentation.
eof-cleanup-proof.json records both before/after hashes and byte equality after
stripping only final newline characters. No algorithm, line containing code,
launch parameter or CMake option changed. The active build and public harness
were rebuilt/relinked; final delivery lib is 26ce0dda2939362b1bf97e4f7c5430a57e505b55604465ee80557bb9129a7252
and harness is c903d77a2eaffb3f74f19370b871764d600a2109010faa1af843732ea3e5db8f.

The complete cuobjdump --dump-sass outputs before and after are byte-identical,
SHA256 f55b3e6519a55abcd53cf281a38053066d7854815a9695b056d5ad3a1e68ecc0.
compare_sass.py additionally verified all 902 sm86 function bodies unchanged;
sass-eof-equivalence.json records every function hash. These CPU-only checks
support applying prior code behavior/performance observations to the rebuilt
artifact while retaining the actual measured binary identities above. A final
HD444 Q90 public-API smoke produced the exact complete JPEG SHA256
c93dc4d7230c3f8646b886a56f5c666fddd60c8a7712d94bfd3b6cbc75ac83e8,
identical to the prior validated output. No full-suite or sanitizer repeat is
claimed for the formatting-only rebuild.

