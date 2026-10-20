/* Restart-free baseline JPEG entropy coding. Original implementation using
 * standard JPEG coding rules; no third-party binary or source is incorporated.
 * Included in gpujpeg_huffman_gpu_encoder.cu to reuse its standard packed LUT. */
#ifdef GPUJPEG_NO_RESTART_GPU
struct gpujpeg_r0_layout {
    int total;
    int blocks[GPUJPEG_MAX_COMPONENT_COUNT];
    int begin[GPUJPEG_MAX_COMPONENT_COUNT];
};

/* Warp-per-block AC coding uses GPUJPEG's paired-coefficient RLE convention:
 * zero symbols occur at each sixteenth zero only if a nonzero follows; the
 * last zero emits EOB. No predictor state crosses warps or blocks. */
__global__ static void
gpujpeg_r0_encode_blocks_warp(const uint64_t *block_list, const int16_t *quantized,
        int first, int count, gpujpeg_r0_layout layout, unsigned int *words,
        unsigned int *bit_counts)
{
#if __CUDA_ARCH__ >= 300
    __shared__ unsigned int private_words[8][64];
    const int lane = threadIdx.x & 31;
    const int warp = threadIdx.x >> 5;
    const int local = blockIdx.x * 8 + warp;
    if (local >= count) return; // Entire invalid warp returns.
    const uint64_t info = block_list[first + local];
    const int component = info & 0x7f;
    const int16_t *block = quantized + (info >> 8);
    int even = block[gpujpeg_huffman_gpu_encoder_order_natural[2 * lane]];
    const int odd = block[gpujpeg_huffman_gpu_encoder_order_natural[2 * lane + 1]];
    const unsigned int lower = (1U << lane) - 1U;
    const unsigned int even_nonzero = 1U | __ballot_sync(0xffffffffU, even != 0);
    const unsigned int odd_nonzero = __ballot_sync(0xffffffffU, odd != 0);
    const unsigned int pairs = even_nonzero | odd_nonzero;
    int zeros_even = 0;
    if (lane) {
        const int previous_pair = 31 - __clz(pairs & lower);
        zeros_even = 2 * (lane - previous_pair) -
                     ((odd_nonzero & (1U << previous_pair)) ? 2 : 1);
    }
    int zeros_odd = even || !lane ? 0 : zeros_even + 1;
    const int run_mask = (pairs & ~lower) ? 15 : 0;
    zeros_even &= run_mask;
    zeros_odd &= run_mask;
    const int ac_table = (info & 0x80) ? 514 : 0;
    int even_table = ac_table;
    if (!lane) {
        int previous = -1;
        if (layout.total == 1) previous = local - 1;
        else {
            const int within = local % layout.total - layout.begin[component];
            if (within > 0) previous = local - 1;
            else if (local >= layout.total)
                previous = local - layout.total + layout.blocks[component] - 1;
        }
        even -= previous < 0 ? 0 : quantized[block_list[first + previous] >> 8];
        even_table += 257;
    }
    if (lane == 31 && !odd) zeros_odd = 16; // AC table's extra EOB entry.
    const unsigned int a = gpujpeg_huffman_gpu_encode_value(zeros_even, even, even_table);
    const unsigned int b = gpujpeg_huffman_gpu_encode_value(zeros_odd, odd, ac_table);
    const unsigned int a_length = a & 31U;
    const unsigned int length = a_length + (b & 31U);
    const uint64_t value = ((uint64_t)(a & ~31U) << 32) |
                          (((uint64_t)(b & ~31U) << 32) >> a_length);
    unsigned int end = length;
    for (int delta = 1; delta < 32; delta <<= 1) {
        const unsigned int prior = __shfl_up_sync(0xffffffffU, end, delta);
        if (lane >= delta) end += prior;
    }
    const unsigned int total = __shfl_sync(0xffffffffU, end, 31);
    unsigned int *out = private_words[warp];
    out[lane] = out[lane + 32] = 0;
    __syncwarp();
    if (length) {
        const unsigned int start = end - length;
        const unsigned int index = start >> 5;
        const unsigned int shift = start & 31U;
        const unsigned int hi = (unsigned int)(value >> 32);
        const unsigned int lo = (unsigned int)value;
        atomicOr(out + index, hi >> shift);
        if (shift + length > 32)
            atomicOr(out + index + 1, shift ? (hi << (32 - shift)) | (lo >> shift) : lo);
        if (shift + length > 64) atomicOr(out + index + 2, lo << (32 - shift));
    }
    __syncwarp();
    for (unsigned int w = lane; w < (total + 31) / 32; w += 32)
        words[(size_t)local * 64 + w] = out[w];
    if (!lane) bit_counts[local] = total;
#endif
}

__device__ static unsigned int
gpujpeg_r0_bit_size(const unsigned int *bits, const unsigned int *offsets, int count)
{
    return bits[count - 1] + offsets[count - 1];
}

__global__ static void
gpujpeg_r0_clear_words(unsigned int *raw, const unsigned int *bits,
                      const unsigned int *offsets, int count)
{
    const unsigned int words = (gpujpeg_r0_bit_size(bits, offsets, count) + 31) / 32 + 1;
    for (unsigned int i = blockIdx.x * blockDim.x + threadIdx.x;
         i < words; i += gridDim.x * blockDim.x) raw[i] = 0;
}

/* Adjacent blocks can share boundary words. Atomic OR merges those disjoint
 * bit fields independently of scheduling. All raw words are cleared first. */
__global__ static void
gpujpeg_r0_pack_bits(const unsigned int *words, const unsigned int *bits,
                    const unsigned int *offsets, int count, unsigned int *raw)
{
#if __CUDA_ARCH__ >= 300
    const unsigned int lane = threadIdx.x & 31;
    const int block = (blockIdx.x * blockDim.x + threadIdx.x) / 32;
    if (block >= count) return;
    const unsigned int length = bits[block];
    const unsigned int shift = offsets[block] & 31;
    const unsigned int first_word = offsets[block] >> 5;
    for (unsigned int w = lane; w < (length + 31) / 32; w += 32) {
        const unsigned int value = words[(size_t)block * 64 + w];
        atomicOr(raw + first_word + w, value >> shift);
        if (shift) atomicOr(raw + first_word + w + 1, value << (32 - shift));
    }
#endif
}

__global__ static void
gpujpeg_r0_pad_scan(unsigned int *raw, const unsigned int *bits,
                   const unsigned int *offsets, int count)
{
    const unsigned int size = gpujpeg_r0_bit_size(bits, offsets, count);
    const unsigned int padding = (0U - size) & 7U;
    if (padding) raw[size >> 5] |= ((1U << padding) - 1U) << (32 - (size & 31) - padding);
}

/* One CTA scans 256 byte flags locally. Only tile counts enter the global
 * scan, avoiding a full-image byte-offset array. The loop bounds are device
 * values, avoiding a host round trip to obtain the actual entropy length. */
template<bool WRITE>
__global__ static void
gpujpeg_r0_stuff_bytes(const unsigned int *raw, const unsigned int *bits,
        const unsigned int *offsets, int count, unsigned int *tile_counts,
        const unsigned int *tile_offsets, const struct gpujpeg_segment *segment,
        uint8_t *output)
{
#if __CUDA_ARCH__ >= 300
    __shared__ unsigned int warp_totals[8];
    const unsigned int lane = threadIdx.x & 31;
    const unsigned int warp = threadIdx.x >> 5;
    const unsigned int bytes = (gpujpeg_r0_bit_size(bits, offsets, count) + 7) / 8;
    const unsigned int tiles = (bytes + 255) / 256;
    for (unsigned int tile = blockIdx.x; tile < tiles; tile += gridDim.x) {
        const unsigned int index = tile * 256 + threadIdx.x;
        const bool valid = index < bytes;
        const unsigned int value = valid ? (raw[index >> 2] >> (24 - (index & 3) * 8)) & 255 : 0;
        const unsigned int flags = __ballot_sync(0xffffffffU, valid && value == 255);
        if (lane == 31) warp_totals[warp] = __popc(flags);
        __syncthreads();
        unsigned int before = __popc(flags & ((1U << lane) - 1U));
        for (unsigned int w = 0; w < warp; ++w) before += warp_totals[w];
        if (WRITE && valid) {
            const size_t dst = segment->data_compressed_index + tile_offsets[tile] + threadIdx.x + before;
            output[dst] = value;
            if (value == 255) output[dst + 1] = 0;
        }
        if (!WRITE && threadIdx.x == 255) {
            unsigned int stuffed = 0;
            for (int w = 0; w < 8; ++w) stuffed += warp_totals[w];
            tile_counts[tile] = min(256U, bytes - tile * 256) + stuffed;
        }
        __syncthreads();
    }
#endif
}

__global__ static void
gpujpeg_r0_allocate_scan(struct gpujpeg_segment *segment, const unsigned int *counts,
        const unsigned int *offsets, int capacity, unsigned int *output_size)
{
    const unsigned int bytes = offsets[capacity - 1] + counts[capacity - 1];
    segment->data_compressed_index = *output_size;
    segment->data_compressed_size = bytes;
    *output_size += bytes;
}

static int
gpujpeg_r0_reserve(struct gpujpeg_huffman_gpu_encoder *s, int count, cudaStream_t stream)
{
    if (count <= s->r0_capacity) return 0;
    cudaFree(s->r0_bits); s->r0_bits = NULL;
    cudaFree(s->r0_offsets); s->r0_offsets = NULL;
    cudaFree(s->r0_tile_counts); s->r0_tile_counts = NULL;
    cudaFree(s->r0_tile_offsets); s->r0_tile_offsets = NULL;
    cudaFree(s->r0_raw); s->r0_raw = NULL;
    cudaFree(s->r0_scan_temp); s->r0_scan_temp = NULL;
    s->r0_capacity = 0;
    if (cudaMalloc((void **)&s->r0_bits, (size_t)count * 4) != cudaSuccess ||
        cudaMalloc((void **)&s->r0_offsets, (size_t)count * 4) != cudaSuccess ||
        cudaMalloc((void **)&s->r0_tile_counts, (size_t)count * 4) != cudaSuccess ||
        cudaMalloc((void **)&s->r0_tile_offsets, (size_t)count * 4) != cudaSuccess ||
        cudaMalloc((void **)&s->r0_raw, (size_t)count * 256 + 4) != cudaSuccess) return -1;
    s->r0_scan_temp_bytes = 0;
    if (cub::DeviceScan::ExclusiveSum(NULL, s->r0_scan_temp_bytes, s->r0_bits,
            s->r0_offsets, count, stream) != cudaSuccess ||
        cudaMalloc(&s->r0_scan_temp, s->r0_scan_temp_bytes) != cudaSuccess) return -1;
    s->r0_capacity = count;
    return 0;
}
#endif

int
gpujpeg_huffman_gpu_encoder_no_restart_supported(const struct gpujpeg_encoder *encoder)
{
#ifdef GPUJPEG_NO_RESTART_GPU
    const struct gpujpeg_coder *coder = &encoder->coder;
    /* 2048 bits/block bounds 8-bit baseline DC/category11, AC/category10 and
     * ZRL codes; it also bounds all 32-bit scans and the stuffed byte count. */
    return coder->cuda_cc_major >= 3 && coder->block_count > 0 &&
           coder->block_count <= (int)(0xffffffffU / 2048U) &&
           coder->data_compressed_allocated_size >= (size_t)coder->block_count * 512;
#else
    (void)encoder;
    return 0;
#endif
}

int
gpujpeg_huffman_gpu_encoder_no_restart(struct gpujpeg_encoder *encoder,
        struct gpujpeg_huffman_gpu_encoder *s, unsigned int *output_byte_count)
{
#ifdef GPUJPEG_NO_RESTART_GPU
    struct gpujpeg_coder *coder = &encoder->coder;
    if (!gpujpeg_huffman_gpu_encoder_no_restart_supported(encoder)) return -1;
    int max_blocks = 0;
    for (int i = 0; i < coder->segment_count; ++i)
        if (coder->segment[i].block_count > max_blocks) max_blocks = coder->segment[i].block_count;
    if (gpujpeg_r0_reserve(s, max_blocks, coder->stream)) return -1;
    gpujpeg_r0_layout layout = {};
    layout.total = 1;
    if (coder->param.interleaved && coder->param.comp_count > 1) {
        layout.total = 0;
        for (int c = 0; c < coder->param.comp_count; ++c) {
            layout.begin[c] = layout.total;
            layout.blocks[c] = coder->param.sampling_factor[c].horizontal *
                               coder->param.sampling_factor[c].vertical;
            layout.total += layout.blocks[c];
        }
    }
    if (cudaMemsetAsync(s->d_gpujpeg_huffman_output_byte_count, 0, 4, coder->stream) != cudaSuccess) return -1;
    for (int i = 0; i < coder->segment_count; ++i) {
        const struct gpujpeg_segment *segment = &coder->segment[i];
        const int n = segment->block_count;
        const int grid = (n + 127) / 128;
        const int persistent_grid = grid < 4096 ? grid : 4096;
        unsigned int *words = (unsigned int *)coder->d_temp_huffman;
        gpujpeg_r0_encode_blocks_warp<<<(n + 7) / 8, 256, 0, coder->stream>>>(coder->d_block_list,
            coder->d_data_quantized, segment->block_index_list_begin, n, layout, words, s->r0_bits);
        if (cub::DeviceScan::ExclusiveSum(s->r0_scan_temp, s->r0_scan_temp_bytes,
                s->r0_bits, s->r0_offsets, n, coder->stream) != cudaSuccess) return -1;
        gpujpeg_r0_clear_words<<<persistent_grid, 256, 0, coder->stream>>>(s->r0_raw, s->r0_bits, s->r0_offsets, n);
        gpujpeg_r0_pack_bits<<<(n + 7) / 8, 256, 0, coder->stream>>>(words, s->r0_bits, s->r0_offsets, n, s->r0_raw);
        gpujpeg_r0_pad_scan<<<1, 1, 0, coder->stream>>>(s->r0_raw, s->r0_bits, s->r0_offsets, n);
        if (cudaMemsetAsync(s->r0_tile_counts, 0, (size_t)n * 4, coder->stream) != cudaSuccess) return -1;
        gpujpeg_r0_stuff_bytes<false><<<persistent_grid, 256, 0, coder->stream>>>(s->r0_raw,
            s->r0_bits, s->r0_offsets, n, s->r0_tile_counts, s->r0_tile_offsets,
            coder->d_segment + i, coder->d_data_compressed);
        if (cub::DeviceScan::ExclusiveSum(s->r0_scan_temp, s->r0_scan_temp_bytes,
                s->r0_tile_counts, s->r0_tile_offsets, n, coder->stream) != cudaSuccess) return -1;
        gpujpeg_r0_allocate_scan<<<1, 1, 0, coder->stream>>>(coder->d_segment + i,
            s->r0_tile_counts, s->r0_tile_offsets, n, s->d_gpujpeg_huffman_output_byte_count);
        gpujpeg_r0_stuff_bytes<true><<<persistent_grid, 256, 0, coder->stream>>>(s->r0_raw,
            s->r0_bits, s->r0_offsets, n, s->r0_tile_counts, s->r0_tile_offsets,
            coder->d_segment + i, coder->d_data_compressed);
        gpujpeg_cuda_check_error("Restart-free GPU Huffman coding", return -1);
    }
    if (cudaMemcpyAsync(output_byte_count, s->d_gpujpeg_huffman_output_byte_count, 4,
            cudaMemcpyDeviceToHost, coder->stream) != cudaSuccess) return -1;
    /* Size is consumed before the caller schedules its compressed-output D2H. */
    return cudaStreamSynchronize(coder->stream) == cudaSuccess ? 0 : -1;
#else
    (void)encoder; (void)s; (void)output_byte_count;
    return -1;
#endif
}
