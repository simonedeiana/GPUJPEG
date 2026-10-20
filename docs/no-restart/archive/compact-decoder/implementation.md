> Archived investigation document. See the [current branch guide](../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# Compact canonical tables and parallel byte unstuffing

These two independent experimental options extend `HUFFMAN_GPU_STAGED_DECODER`:

| CMake option | Purpose | Default |
|---|---|---|
| `HUFFMAN_GPU_COMPACT_TABLES` | Small canonical tables instead of expanded lookup tables | OFF |
| `HUFFMAN_GPU_PARALLEL_UNSTUFF` | Cooperative FF00 removal and a word-based Huffman reader | OFF |

Both require `HUFFMAN_GPU_STAGED_DECODER=ON`, CUDA 9+, and SM3 or newer. A
configured input option rejects an older GPU at runtime rather than attempting
to execute unsupported shuffle/ballot code. The original staged option alone
retains its previous fallback. No-restart and other images selected for CPU
Huffman decoding still use that existing CPU path.

## Compact canonical tables

Each of four tables has this independent representation:

* Sixteen 32-bit exclusive upper bounds for a 16-bit lookahead (64 bytes).
* Sixteen 16-bit first canonical codes (32 bytes).
* Sixteen 16-bit first symbol-array indices (32 bytes).
* Up to 256 byte-sized symbols (256 bytes).

This is **384 bytes/table, 1,536 bytes total**, replacing the four full 65,536-entry
16-bit tables (524,288 bytes) and quick table (8,192 bytes). Compact mode does not
allocate the expanded/quick arrays, build them on the GPU, or refresh the quick
table in global constant memory. The natural-order table is still used by RLE.
These sizes describe the lookup representation, not all decoder table memory:
the public decoder's existing source-DHT allocations and reader uploads remain.
With `HUFFMAN_GPU_CONST_TABLES=ON`, that fixed natural-order table may remain
constant; canonical Huffman data remain private to each decoder instance.

On a DHT change the CPU derives canonical codes from the code-length counts.
After adding the count for length `L`, the exclusive bound is
`next_code << (16-L)`. Empty lengths repeat the previous bound, preserving a
nondecreasing array. The 32-bit type represents the exclusive value 65536 without
truncation. A four-decision lower-bound tree chooses the first bound greater than
the lookahead; repeated bounds cannot select an empty length. Prefixes at or above
the final bound return the same safe EOB token used for invalid full-LUT entries,
before any symbol-array access. The symbol index is
`first_symbol[L] + prefix_code - first_code[L]`.

Host-side canonical staging belongs to the persistent per-instance table cache,
so its asynchronous copy does not refer to a stack object. The cache snapshot is
committed after the copy succeeds; later Huffman kernels use the same CUDA stream.
Tables are uploaded only on first use or content change, including different DHT
contents using the same IDs. Device canonical storage is freed with the decoder.

## Parallel FF00 unstuffing

GPUJPEG's existing CPU parser has already separated restart segments and removed
the RST marker bytes. This port therefore needs only remove the zero **immediately
following an FF within a segment**. It does not scan the image globally for RSTs.

One warp processes each restart segment in 32-byte tiles. Each lane tests its
byte, inspecting the preceding source byte when needed. The first byte never
looks into the preceding segment; the predecessor at a tile boundary remains
inside the current segment. A ballot plus population counts assigns disjoint
retained-byte output positions. An ordinary zero is preserved. Partial final
tiles still have full-warp ballot participation.

Every segment has a separate padded output slot, avoiding a device-wide prefix
scan or a host readback for output allocation:

```
slot_begin = align_up(original_compressed_begin, 16) + 32 * segment_index
```

The extra 32 bytes per segment accommodate start alignment and sixteen zero guard
bytes following its compacted data. Original segment ranges are ordered/disjoint;
the padding therefore prevents adjacent output-slot overlap. The allocated
payload capacity is `compressed_bytes + 32*segment_count + 32`, plus
`4*segment_count` bytes of retained-length metadata. These allocations are private
to the decoder, grow/reuse their high-water capacity, and are freed on destruction.
Size additions and multiplications are checked. There is no new D2H copy.

The Huffman thread reads aligned 32-bit words through a 64-bit reservoir. It no
longer tests FF bytes or stages byte chunks in shared memory. Reads are bounded
by `ceil(retained_size/4)`; the zero guard makes the partial final word safe, and
exhausted lookahead supplies zero without loading another word. This is a memory
bound for the experimental reader, not a claim of comprehensive malformed-JPEG
validation. Existing invalid-input behavior is not replaced with a new error API.

## What is and is not being compared

The four combinations C0U0, C1U0, C0U1, C1U1 keep the same staged token, RLE,
DC-scan, IDCT and color-output code. Since the input-reader wrapper was refactored,
use a newly built C0U0 control from this same source revision for the 2-by-2
ablation. An older staged binary is a historical comparison, not the primary
isolation control. Enabling unstuff includes both the compaction pass and the
word-reservoir reader that its output enables; these are measured together.

Runtime kernel names expose the two template booleans:
`gpujpeg_huffman_staged_tokens_kernel<128, COMPACT, UNSTUFFED>`. The separate
`gpujpeg_huffman_parallel_unstuff_kernel` must appear when U1 actually executes.
Build flags alone do not establish that a particular JPEG used the GPU path.

The design transfers two observed techniques from a Fastvideo binary, independently
implemented against GPUJPEG data structures. Fastvideo's global structural
markup/scans, exact canonical table layout, MCU token planes and fused IDCT/output
are different. GPUJPEG retains its own CPU restart parser, staged token format,
coefficient layout and output pipeline. Full-codec comparisons must include H2D
and D2H transfers on identical JPEG files and count the added unstuff kernel.
