# Restart-free GPU Huffman encoding

GPUJPEG previously copied the complete quantized coefficient image to the CPU
when restart_interval was zero. Its CPU Huffman encoder then processed each
scan serially. This change keeps restart-free entropy coding on the GPU.

HUFFMAN_GPU_NO_RESTART defaults to ON. The GPU implementation is compiled with
CUDA 11 or newer, uses the toolkit's CUB DeviceScan and requires SM 3 or newer.
Older toolkits/devices retain the existing CPU path. Set the CMake option OFF
to restore CPU entropy coding for restart-free images. Positive restart
intervals continue through the original GPU implementation.

## Relationship to the inspected nvJPEG implementation

REA analysis of the installed nvJPEG library and runtime traces identified
independent DC/AC coding, a scan of block bit lengths, shifted block assembly
with atomic OR into a zeroed stream, then a separate stuffing-length scan and
byte-stuffing pass. SM86 disassembly confirmed atomic OR in block assembly.
These are the architectural techniques reproduced here with independent code.
No vendor source, binary instructions or proprietary scratch layout was copied.
The vendor stages and measured execution evidence are documented separately in
the analysis report.

## Data flow

1. A warp per 8x8 block generates its MSB-first Huffman bitstream and bit count.
   DC is the current raw DC coefficient minus the preceding raw DC coefficient
   of the same component in JPEG coding order. The first block in each
   component predicts zero. There is no dependency on another thread's
   reconstructed predictor. AC uses GPUJPEG's standard Huffman tables, ZRL
   symbols and EOB rules. Each lane loads two zigzag coefficients. Ballots
   identify nonzero runs; a warp scan allocates each lane's two codewords in
   a shared private bitstream. Atomic OR merges shared-word boundaries, then
   the warp stores its private words coalesced to device scratch. Eight warps
   per CTA handle eight independent blocks.
2. CUB exclusive-sums block bit counts into scan-relative start-bit offsets.
3. A kernel clears the used raw stream words, including one guard word.
   A warp per block shifts its private words into the complete scan, combining
   overlapping boundary words with atomic OR. These are disjoint bit fields.
4. The last scan byte receives one-bit padding only when its bit count is not
   divisible by eight. Padding is applied before stuffing.
5. A CTA handles a tile of 256 source bytes. Warp ballots and shared warp
   totals count FF bytes. Only one integer per tile enters a second CUB scan.
   Trailing tile counts are zero; padding outside the valid byte range is
   excluded. A second tiled pass writes source bytes and 00 after each FF to
   their disjoint output offsets.
6. A one-thread kernel records the scan's actual byte size and compact output
   base. Noninterleaved scans repeat these steps independently, resetting DC
   prediction and final-byte padding. Their entropy bytes are concatenated in
   the output allocation, with SOS headers added by the existing host writer.
   No restart markers are generated.

All launches use the caller's coder stream. The final four-byte output-size
download is explicitly synchronized before the caller schedules the compressed
output download. The host downloads compressed bytes and segment descriptors,
rather than the full coefficient image. Existing JPEG header, table, EOI and
public output-buffer handling remain in the host writer. Segment-info hooks
and the old final-RST removal are skipped for restart-free scans.

## Scratch memory and bounds

The opaque GPU Huffman state owns reusable allocation capacity. Its arrays grow
to the largest scan block count seen by that encoder instance and are freed
when the instance is destroyed. No new process-wide device scratch is added.

For capacity N blocks, extra device storage is 272*N + 4 bytes plus CUB's
queried temporary allocation: 256*N + 4 bytes for the assembled raw stream and
four N-element uint32 arrays for bit counts, bit offsets, tile counts and tile
offsets. The existing d_temp_huffman allocation holds each block's private
64-word (256-byte) stream. CUB temporary storage is reused sequentially for
the two prefix scans. Noninterleaved scans reuse the same capacity.

The bound is 2048 unstuffed bits per 8-bit baseline block, exceeding the
1665-bit bound from category-11 DC and 63 category-10 AC values with 16-bit
Huffman prefixes. ZRL does not increase that maximum: it replaces multiple
zero coefficients. FF stuffing can at most double bytes. The new route also
checks that the existing output allocation covers 512 bytes per image block.

Inputs with more than 2,097,151 coded blocks use the existing CPU route, keeping
every bit-offset prefix within uint32 range. Allocation failure returns an
encoding error with any partially allocated scratch retained for cleanup;
it does not silently retry a partially executed frame on the CPU. These
bounds concern supported encoder output, not general malformed JPEG decoding.

## Validation and performance scope

The independent validation records, sanitizers, exact source/binary identities
and host-input/host-output benchmarks accompany this source. Whole-call
measurements must include both transfers and final CPU JPEG assembly, and must
use the same source samples, sampling and quality for each implementation.
First-frame allocation/JIT costs and warm-call measurements are separate.

Full racecheck can report warnings in upstream's unchanged DCT kernel; report
those separately from checks of the newly introduced Huffman/packing/stuffing
kernels. Do not describe the entire encoder as racecheck-clean based on a
filtered check.

The initial one-thread-per-block coding prototype is preserved separately as
an ablation. The final variant uses warp-parallel AC coding, following the
architecture observed in nvJPEG while reusing GPUJPEG's own RLE convention.
Scratch-memory traffic, small-image launch overhead and serial scan setup for
planar images remain possible limitations.
