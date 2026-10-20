> Archived investigation document. See the [current branch guide](../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# Experimental GPU decoding without restart markers

Build with `-DHUFFMAN_GPU_NO_RESTART_DECODER=ON`. The option defaults OFF while
this implementation is evaluated. The locked encoder is unchanged. Decoder
changes are developed after tag `encoder-r0-locked-20261020`, commit
`d94ed55e2922332958287a897bdcd83a7205a95e`.

The algorithm is independently implemented from the general speculative
decoding/overflow synchronization approach described by Weissenberger and
Schmidt, *Accelerating JPEG Decompression on GPUs*, arXiv:2111.09219. The
jpeggpu project demonstrates that approach. No GPL jpeggpu source is included
in this implementation; GPUJPEG's existing license is retained.

## Boundaries and correctness

An entropy decoder's state is `(next_bit, next_coefficient, MCU_phase)`.
`next_coefficient` is the next zigzag slot, 0 through 63; slot 0 selects DC.
`MCU_phase` identifies the component and block within an interleaved MCU.
Separate scans have one phase. DC values are reconstructed later, so a
running DC predictor is unnecessary in this synchronization state.

The GPU removes FF00 stuffing using 256-byte tiles, a count scan, and a
disjoint scatter. The host already identifies scan extents through GPUJPEG's
normal JPEG reader. It does not decode Huffman symbols or create restart
markers. Compressed input upload uses the existing partially pinned transfer
helper, accounting for each scan's offset into the registered host region.

The unstuffed stream is divided into fixed 1024-bit chunks. Every chunk is
first decoded from a speculative DC/phase-zero anchor. Invalid speculative
prefixes advance one bit without emitting coefficients; this only defines a
bounded recovery trajectory. Final output never silently uses this recovery
before its expected coefficients have been decoded.

Each chunk records its end state and the number of coefficient positions
advanced, including zero runs and EOB completion. The first chunk's anchor is
the real scan start. Adjacent groups are reconciled at widths 2, 4, 8, etc.
Each group already has internally consistent halves from the preceding level.
One thread propagates the left half's end state through the right half.

If the recomputed end state exactly equals the right half's recorded state,
the remaining suffix is reusable: future decoding is deterministic from that
state. The visited chunk's coefficient count is updated even when its end
state matches, because a whole MCU cycle can change counts without changing
phase. If equality never occurs, the thread repairs the entire right half.
Different groups are disjoint and successive levels use ordered launches.

By induction every merged group is internally consistent with its first
anchor. The final group starts at the real scan beginning, so all boundaries
are correct. Correctness therefore does not assume that a Huffman stream will
resynchronize within an arbitrary fixed overlap. Speed does depend on early
convergence; a poorly synchronizing stream can cause much more serial repair.

## Parallel output and DC reconstruction

A 64-bit exclusive scan of chunk coefficient counts allocates output ranges.
The total must equal `64 * scan_block_count`. Each chunk then decodes again
from the reconciled preceding boundary, writing its nonzero AC values through
GPUJPEG's block list and natural-order map. The whole coefficient buffer is
cleared first, so EOB/ZRL need not write individual zeros.

DC differences are gathered into component-specific coding order. Signed
64-bit inclusive scans reconstruct DC values; a scatter places them into the
existing quantized coefficient layout. Physical component raster order must
not be substituted for MCU coding order in 420/422. Separate component scans
reset prediction independently. GPUJPEG's existing IDCT and output conversion
follow unchanged.

An exact zero-region shortcut handles an observed synchronization pathology.
The host verifies that every active DC table begins with the zero-difference
symbol at the same all-zero code length, and every AC table begins with EOB
at a shared all-zero code length. A GPU chunk must then prove that its entire
bit interval plus a 32-bit guard is zero. For those chunks the repeated DC0/EOB
transitions can be counted arithmetically, including a partial starting block
and a final symbol that crosses the chunk boundary. This produces the same
bit position, zigzag slot, phase and coefficient count as individual decoding.
Their zero differences and AC values already exist in the cleared buffers,
so the output pass can skip stores. Nonzero regions and unproven tails use
normal decoding. This avoids treating merely similar table IDs as equivalent.

All new scans use explicit 256-thread tile scans, recursively scanned tile
totals and uniform addition. Recursion scratch is bounded at allocation time;
there is no new process-global scratch or host polling for convergence.

## Scope, memory and failure handling

The initial eligible route requires CUDA 11+, SM3+, restart interval zero,
the existing supported scan organization, at most 16 blocks per interleaved
MCU, and matching DC/AC table indices in 0 or 1 for every component. The
constant-table decoder build uses the old path. Images above 2,097,151 coded
blocks or with a scan exceeding 16 MiB of stored entropy use the old route.
Positive restart intervals also use the existing implementation.

Per instance, let B be stored scan-byte capacity, C=ceil(B/128), U=ceil(B/256)
and N be coefficient-block capacity. New buffers require approximately
`2*B + 24 + 32*C + 8*U + 16*N + W` bytes, excluding the existing decoder
tables/buffers. W holds recursively allocated tile totals/offsets, using
`sum(16*ceil(max(N,C,U)/256^level) + 16)` bytes down to one tile. Buffers grow
without shrinking and are released with the decoder. Scans reuse them.

All work uses the caller's stream. The only new host readback is a four-byte
status after entropy reconstruction. Category/run/count checks bound output
writes; DC reconstruction checks the int16 destination range. Allocation or
execution failure returns an error, rather than accepting speculative output.
This is not a replacement for full malformed-JPEG validation.

The experimental Huffman timer includes compressed upload and preparatory
work. Whole-call host timings and Nsight stages should be used for performance
assessment. Tiny inputs can lose to CPU decoding because of launch overhead;
large low-entropy inputs can also suffer from poor synchronization. The first
validated configurations are baseline sequential grayscale and three-component
444/422/420, including noninterleaved scans and optimized Huffman tables.
