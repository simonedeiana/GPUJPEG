/* Independently implemented hierarchical speculative JPEG decoding.
 * Algorithmic reference: Weissenberger/Schmidt, arXiv:2111.09219.
 * No jpeggpu implementation is incorporated. */
#ifdef GPUJPEG_NO_RESTART_DECODER


enum { DR0_CHUNK_BITS = 1024, DR0_PHASES = 16 };
struct dr0_boundary { unsigned int bit; int coefficient, phase; };
struct dr0_layout {
    int phases, blocks, first, zero_dc_bits, zero_ac_bits;
    int table[DR0_PHASES], component[DR0_PHASES];
    int dc_base[DR0_PHASES], dc_stride[DR0_PHASES], dc_within[DR0_PHASES];
};
struct gpujpeg_decoder_r0_state {
    uint8_t *input, *raw;
    unsigned int *raw_size, *tile_count, *tile_offset, *error, *zero_chunks;
    dr0_boundary *boundary;
    uint64_t *counts, *offsets;
    int64_t *dc, *dc_sum;
    void *workspace;
    size_t workspace_bytes;
    int byte_capacity, block_capacity;
};

/* Scans have an explicit recursion bound and own no process-global state. */
template<typename T, bool INCLUSIVE>
__global__ static void dr0_scan_tiles(const T *input, T *output, int count, T *totals)
{
    __shared__ T warps[8];
    const int index = blockIdx.x * 256 + threadIdx.x, lane = threadIdx.x & 31, warp = threadIdx.x >> 5;
    const T value = index < count ? input[index] : T(0);
    T sum = value;
    for (int delta = 1; delta < 32; delta *= 2) {
        const T prior = __shfl_up_sync(0xffffffffU, sum, delta);
        if (lane >= delta) sum += prior;
    }
    if (lane == 31) warps[warp] = sum;
    __syncthreads();
    for (int i = 0; i < warp; ++i) sum += warps[i];
    if (index < count) output[index] = INCLUSIVE ? sum : sum - value;
    if (threadIdx.x == 255) totals[blockIdx.x] = sum;
}

template<typename T>
__global__ static void dr0_scan_add(T *output, int count, const T *offsets)
{
    const int index = blockIdx.x * blockDim.x + threadIdx.x;
    if (index < count) output[index] += offsets[index / 256];
}

template<typename T, bool INCLUSIVE>
static void dr0_scan(const T *input, T *output, int count, uint8_t *workspace, cudaStream_t stream)
{
    const int groups = (count + 255) / 256;
    T *totals = (T *)workspace, *offsets = totals + groups;
    dr0_scan_tiles<T, INCLUSIVE><<<groups, 256, 0, stream>>>(input, output, count, totals);
    if (groups > 1) {
        dr0_scan<T, false>(totals, offsets, groups, (uint8_t *)(offsets + groups), stream);
        dr0_scan_add<<<groups, 256, 0, stream>>>(output, count, offsets);
    }
}

static void dr0_release(gpujpeg_decoder_r0_state *s)
{
    if (!s) return;
    cudaFree(s->input); cudaFree(s->raw); cudaFree(s->raw_size);
    cudaFree(s->tile_count); cudaFree(s->tile_offset); cudaFree(s->error); cudaFree(s->zero_chunks);
    cudaFree(s->boundary); cudaFree(s->counts); cudaFree(s->offsets);
    cudaFree(s->dc); cudaFree(s->dc_sum); cudaFree(s->workspace);
    free(s);
}

static int dr0_reserve(gpujpeg_decoder_r0_state *&state, int bytes, int blocks, cudaStream_t stream)
{
    if (state && state->byte_capacity >= bytes && state->block_capacity >= blocks) return 0;
    if (state) { bytes = std::max(bytes, state->byte_capacity); blocks = std::max(blocks, state->block_capacity); }
    dr0_release(state); state = (gpujpeg_decoder_r0_state *)calloc(1, sizeof(*state));
    if (!state) return -1;
    auto *s = state;
    const int tiles = (bytes + 255) / 256, chunks = (bytes + DR0_CHUNK_BITS / 8 - 1) / (DR0_CHUNK_BITS / 8);
#define DR0_ALLOC(member, count) if (cudaMalloc((void **)&s->member, (size_t)(count) * sizeof(*s->member)) != cudaSuccess) return -1
    DR0_ALLOC(input, bytes); DR0_ALLOC(raw, bytes + 16); DR0_ALLOC(raw_size, 1);
    DR0_ALLOC(tile_count, tiles); DR0_ALLOC(tile_offset, tiles); DR0_ALLOC(error, 1);
    DR0_ALLOC(boundary, chunks); DR0_ALLOC(counts, chunks); DR0_ALLOC(offsets, chunks); DR0_ALLOC(zero_chunks, chunks);
    DR0_ALLOC(dc, blocks); DR0_ALLOC(dc_sum, blocks);
#undef DR0_ALLOC
    s->workspace_bytes = 0;
    int level = std::max(blocks, std::max(chunks, tiles));
    do { level = (level + 255) / 256; s->workspace_bytes += (size_t)level * 16 + 16; } while (level > 1);
    if (cudaMalloc(&s->workspace, s->workspace_bytes) != cudaSuccess) return -1;
    s->byte_capacity = bytes; s->block_capacity = blocks;
    return 0;
}

/* Only stuffing bytes are removed, with a count scan allocating disjoint tiles. */
template<bool WRITE>
__global__ static void dr0_unstuff(const uint8_t *input, int bytes, unsigned int *counts,
        const unsigned int *offsets, uint8_t *raw)
{
    __shared__ unsigned int totals[8];
    const int i = blockIdx.x * 256 + threadIdx.x;
    const int lane = threadIdx.x & 31, warp = threadIdx.x >> 5;
    const bool keep = i < bytes && !(i && input[i] == 0 && input[i - 1] == 255);
    const unsigned int mask = __ballot_sync(0xffffffffU, keep);
    if (lane == 31) totals[warp] = __popc(mask);
    __syncthreads();
    unsigned int before = __popc(mask & ((1U << lane) - 1));
    for (int w = 0; w < warp; ++w) before += totals[w];
    if (WRITE && keep) raw[offsets[blockIdx.x] + before] = input[i];
    if (!WRITE && threadIdx.x == 255) counts[blockIdx.x] = before + (keep ? 1 : 0);
}

__global__ static void dr0_finish_unstuff(const unsigned int *counts, const unsigned int *offsets,
        int tiles, unsigned int *size)
{ *size = offsets[tiles - 1] + counts[tiles - 1]; }

__device__ __forceinline__ static unsigned int dr0_peek(const uint8_t *raw, unsigned int bit)
{
    const unsigned int *p = (const unsigned int *)raw + (bit >> 5);
    const unsigned int a = __byte_perm(p[0], 0, 0x0123), shift = bit & 31;
    return shift ? (a << shift) | (__byte_perm(p[1], 0, 0x0123) >> (32 - shift)) : a;
}

/* A false anchor can hit unused prefixes. Advance one bit without emitting a
 * value. Such speculative recovery is never accepted silently in final output. */
__device__ __forceinline__ static int dr0_symbol(const uint8_t *raw, unsigned int limit, const uint16_t *tables,
        const uint16_t *quick, const dr0_layout &layout, dr0_boundary &s, int &advance, int &value, int &position)
{
    const unsigned int bits = dr0_peek(raw, s.bit);
    const int table = layout.table[s.phase] * 2 + (s.coefficient != 0);
    unsigned int entry = quick[(table << QUICK_CHECK_BITS) + (bits >> (32 - QUICK_CHECK_BITS))];
    if (!(entry & 0x1f0)) entry = tables[(table << 16) + (bits >> 16)];
    const int code = (entry >> 4) & 31, size = entry & 15, run = (entry >> 9);
    advance = 0; value = 0; position = s.coefficient;
    bool valid = code > 0 && s.bit + code + size <= limit;
    if (!s.coefficient) {
        valid &= size <= 11;
        advance = 1;
    } else if (!size) {
        valid &= run == 16 || run == 64;
        advance = run == 64 ? 64 - s.coefficient : 16;
    } else {
        valid &= size <= 10;
        advance = run;
        position += run - 1;
    }
    valid &= advance > 0 && s.coefficient + advance <= 64;
    if (!valid) { ++s.bit; advance = 0; return 0; }
    if (size) {
        const unsigned int amplitude = (bits << code) >> (32 - size);
        value = amplitude < (1U << (size - 1)) ? (int)amplitude + 1 - (1 << size) : amplitude;
    }
    const int write = !s.coefficient || size;
    s.bit += code + size;
    s.coefficient += advance;
    if (s.coefficient == 64) { s.coefficient = 0; s.phase = (s.phase + 1) % layout.phases; }
    return write ? 2 : 1;
}

__device__ static dr0_boundary dr0_decode_chunk(const uint8_t *raw, unsigned int limit,
        const uint16_t *tables, const uint16_t *quick, const dr0_layout &layout,
        dr0_boundary state, unsigned int end, uint64_t &count, bool zero)
{
    count = 0;
    if (zero && state.bit < end) {
        // The inspected interval plus a 32-bit guard is all zero. Every table
        // decodes the same zero-DC and EOB prefixes, so count exact repetitions.
        if (state.coefficient) {
            state.bit += layout.zero_ac_bits;
            count += 64 - state.coefficient; state.coefficient = 0;
            state.phase = (state.phase + 1) % layout.phases;
        }
        if (state.bit < end) {
            const unsigned int pairs = (end - state.bit) / (layout.zero_dc_bits + layout.zero_ac_bits);
            state.bit += pairs * (layout.zero_dc_bits + layout.zero_ac_bits);
            count += (uint64_t)pairs * 64;
            state.phase = (state.phase + pairs) % layout.phases;
            if (state.bit < end) {
                state.bit += layout.zero_dc_bits; ++count; state.coefficient = 1;
                if (state.bit < end) {
                    state.bit += layout.zero_ac_bits; count += 63; state.coefficient = 0;
                    state.phase = (state.phase + 1) % layout.phases;
                }
            }
        }
        return state;
    }
    while (state.bit < end && state.bit < limit) {
        int advance, value, position;
        dr0_symbol(raw, limit, tables, quick, layout, state, advance, value, position);
        count += advance;
    }
    return state;
}

__global__ static void dr0_speculate(const uint8_t *raw, const unsigned int *bytes,
        const uint16_t *tables, const uint16_t *quick, dr0_layout layout,
        int chunks, dr0_boundary *ends, uint64_t *counts, unsigned int *zero_chunks)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= chunks) return;
    const unsigned int limit = *bytes * 8;
    dr0_boundary state = { min((unsigned int)i * DR0_CHUNK_BITS, limit), 0, 0 };
    bool zero = layout.zero_dc_bits && (i + 1U) * DR0_CHUNK_BITS + 32 <= limit;
    if (zero) {
        unsigned int any = 0;
        const unsigned int *words = (const unsigned int *)raw + i * (DR0_CHUNK_BITS / 32);
        for (int j = 0; j <= DR0_CHUNK_BITS / 32; ++j) any |= words[j];
        zero = any == 0;
    }
    zero_chunks[i] = zero;
    ends[i] = dr0_decode_chunk(raw, limit, tables, quick, layout, state,
            (i + 1U) * DR0_CHUNK_BITS, counts[i], zero);
}

/* Each group already has consistent internal boundaries. Repair its right
 * half from the left half's actual end state. Exact state equality proves the
 * remaining right suffix reusable. Without convergence, repair all of it.
 * Groups are disjoint; separate launches order successive doubling levels. */
__global__ static void dr0_reconcile(const uint8_t *raw, const unsigned int *bytes,
        const uint16_t *tables, const uint16_t *quick, dr0_layout layout,
        int chunks, int width, dr0_boundary *ends, uint64_t *counts, const unsigned int *zero_chunks)
{
    const int begin = (blockIdx.x * blockDim.x + threadIdx.x) * width;
    const int middle = begin + width / 2, end = min(begin + width, chunks);
    if (middle >= chunks) return;
    dr0_boundary state = ends[middle - 1];
    for (int i = middle; i < end; ++i) {
        const dr0_boundary old = ends[i];
        state = dr0_decode_chunk(raw, *bytes * 8, tables, quick, layout, state,
                (i + 1U) * DR0_CHUNK_BITS, counts[i], zero_chunks[i] != 0);
        ends[i] = state;
        if (state.bit == old.bit && state.phase == old.phase && state.coefficient == old.coefficient) break;
    }
}

__global__ static void dr0_check_count(const uint64_t *counts, const uint64_t *offsets,
        int chunks, int blocks, unsigned int *error)
{ if (counts[chunks - 1] + offsets[chunks - 1] != (uint64_t)blocks * 64) *error = 1; }

__global__ static void dr0_write(const uint8_t *raw, const unsigned int *bytes,
        const uint16_t *tables, const uint16_t *quick, const int *natural, dr0_layout layout,
        int chunks, const dr0_boundary *ends, const uint64_t *offsets,
        const uint64_t *block_list, int16_t *quantized, int64_t *dc, unsigned int *error, const unsigned int *zero_chunks)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= chunks || atomicAdd(error, 0U) || zero_chunks[i]) return;
    dr0_boundary state = i ? ends[i - 1] : dr0_boundary{0, 0, 0};
    uint64_t out = offsets[i];
    const unsigned int end = (i + 1U) * DR0_CHUNK_BITS, limit = *bytes * 8;
    while (state.bit < end && state.bit < limit && out < (uint64_t)layout.blocks * 64) {
        const int phase = state.phase, k = state.coefficient;
        int advance, value, position;
        const int result = dr0_symbol(raw, limit, tables, quick, layout, state, advance, value, position);
        if (!result) { atomicExch(error, 2); return; }
        if (result == 2) {
            const int block = (int)(out >> 6);
            if (!k) {
                const int index = layout.dc_base[phase] + (block / layout.phases) *
                    layout.dc_stride[phase] + layout.dc_within[phase];
                dc[index] = value;
            } else quantized[(block_list[layout.first + block] >> 8) + natural[position]] = (int16_t)value;
        }
        out += advance;
    }
}

__global__ static void dr0_restore_dc(const uint64_t *block_list, dr0_layout layout,
        const int64_t *dc, int16_t *quantized, unsigned int *error)
{
    const int block = blockIdx.x * blockDim.x + threadIdx.x;
    if (block >= layout.blocks || atomicAdd(error, 0U)) return;
    const int phase = block % layout.phases;
    const int index = layout.dc_base[phase] + (block / layout.phases) *
        layout.dc_stride[phase] + layout.dc_within[phase];
    const int64_t value = dc[index];
    if (value < -32768 || value > 32767) { atomicExch(error, 3U); return; }
    quantized[block_list[layout.first + block] >> 8] = (int16_t)value;
}
#endif

int gpujpeg_huffman_gpu_decoder_no_restart_supported(const struct gpujpeg_decoder *decoder)
{
#if defined(GPUJPEG_NO_RESTART_DECODER) && !defined(HUFFMAN_GPU_CONST_TABLES)
    const auto *c = &decoder->coder;
    if (c->param.restart_interval || c->cuda_cc_major < 3 || c->block_count < 1 || c->block_count > 2097151 ||
        decoder->segment_count != (c->param.interleaved ? 1 : c->param.comp_count)) return 0;
    int phases = 0;
    for (int i = 0; i < c->param.comp_count; ++i) {
        phases += c->param.sampling_factor[i].horizontal * c->param.sampling_factor[i].vertical;
        if (decoder->comp_table_huffman_map[i][0] != decoder->comp_table_huffman_map[i][1] ||
            decoder->comp_table_huffman_map[i][0] < 0 || decoder->comp_table_huffman_map[i][0] > 1) return 0;
    }
    if (c->param.interleaved && phases > DR0_PHASES) return 0;
    for (int i = 0; i < decoder->segment_count; ++i)
        if (!c->segment[i].data_compressed_size || c->segment[i].data_compressed_size > 16 * 1024 * 1024) return 0;
    return 1;
#else
    (void)decoder; return 0;
#endif
}

int gpujpeg_huffman_gpu_decoder_no_restart(struct gpujpeg_decoder *decoder)
{
#ifdef GPUJPEG_NO_RESTART_DECODER
    auto *c = &decoder->coder;
    auto *h = decoder->huffman_gpu_decoder;
    if (!gpujpeg_huffman_gpu_decoder_no_restart_supported(decoder)) return -1;
    size_t max_bytes = 0;
    for (int i = 0; i < decoder->segment_count; ++i) max_bytes = std::max(max_bytes, c->segment[i].data_compressed_size);
    if (dr0_reserve(h->r0, (int)max_bytes, c->block_count, c->stream)) { fprintf(stderr, "DR0 reserve failed bytes=%zu blocks=%d\n", max_bytes, c->block_count); return -1; }
    auto *s = h->r0;
    gpujpeg_huffman_decoder_table_kernel<<<256, 256, 0, c->stream>>>(*h,
        decoder->d_table_huffman[0][0], decoder->d_table_huffman[0][1],
        decoder->d_table_huffman[1][0], decoder->d_table_huffman[1][1]);
    cudaMemsetAsync(c->d_data_quantized, 0, c->data_size * sizeof(int16_t), c->stream);
    cudaMemsetAsync(s->error, 0, 4, c->stream);
    for (int scan = 0; scan < decoder->segment_count; ++scan) {
        const auto *seg = &c->segment[scan];
        dr0_layout layout = {};
        layout.blocks = seg->block_count; layout.first = seg->block_index_list_begin;
        int dc_count[GPUJPEG_MAX_COMPONENT_COUNT] = {}, dc_base[GPUJPEG_MAX_COMPONENT_COUNT] = {};
        const int first_comp = c->param.interleaved ? 0 : seg->scan_index;
        const int last_comp = c->param.interleaved ? c->param.comp_count : first_comp + 1;
        int phase_count[GPUJPEG_MAX_COMPONENT_COUNT] = {};
        for (int comp = first_comp; comp < last_comp; ++comp) {
            phase_count[comp] = c->param.interleaved ? c->param.sampling_factor[comp].horizontal *
                c->param.sampling_factor[comp].vertical : 1;
            layout.phases += phase_count[comp];
        }
        int phase = 0, dc_total = 0;
        for (int comp = first_comp; comp < last_comp; ++comp) {
            dc_count[comp] = layout.blocks / layout.phases * phase_count[comp];
            dc_base[comp] = dc_total; dc_total += dc_count[comp];
            for (int j = 0; j < phase_count[comp]; ++j, ++phase) {
                layout.table[phase] = decoder->comp_table_huffman_map[comp][0];
                layout.component[phase] = comp; layout.dc_base[phase] = dc_base[comp];
                layout.dc_stride[phase] = phase_count[comp]; layout.dc_within[phase] = j;
            }
        }
        int zero_dc = -1, zero_ac = -1;
        for (int comp = first_comp; comp < last_comp; ++comp) {
            const int table = decoder->comp_table_huffman_map[comp][0];
            int lengths[2] = {};
            for (int kind = 0; kind < 2; ++kind) {
                const auto &definition = decoder->table_huffman[table][kind];
                for (int bits = 1; bits <= 16; ++bits)
                    if (definition.bits[bits]) { lengths[kind] = definition.huffval[0] == 0 ? bits : 0; break; }
            }
            if (zero_dc < 0) { zero_dc = lengths[0]; zero_ac = lengths[1]; }
            else if (zero_dc != lengths[0] || zero_ac != lengths[1]) { zero_dc = zero_ac = 0; break; }
        }
        if (zero_dc > 0 && zero_ac > 0) { layout.zero_dc_bits = zero_dc; layout.zero_ac_bits = zero_ac; }
        const int bytes = (int)seg->data_compressed_size, tiles = (bytes + 255) / 256;
        const int chunks = (bytes + DR0_CHUNK_BITS / 8 - 1) / (DR0_CHUNK_BITS / 8);
        const size_t pinned = c->data_compressed_pinned_sz > seg->data_compressed_index ?
            c->data_compressed_pinned_sz - seg->data_compressed_index : 0;
        if (gpujpeg_cuda_memcpy_async_partially_pinned(s->input,
                c->data_compressed + seg->data_compressed_index, bytes,
                cudaMemcpyHostToDevice, c->stream, pinned) != cudaSuccess) return -1;
        cudaMemsetAsync(s->raw, 0, bytes + 16, c->stream);
        cudaMemsetAsync(s->dc, 0, (size_t)layout.blocks * sizeof(*s->dc), c->stream);
        dr0_unstuff<false><<<tiles, 256, 0, c->stream>>>(s->input, bytes, s->tile_count, s->tile_offset, s->raw);
        dr0_scan<unsigned int, false>(s->tile_count, s->tile_offset, tiles, (uint8_t *)s->workspace, c->stream);
        dr0_unstuff<true><<<tiles, 256, 0, c->stream>>>(s->input, bytes, s->tile_count, s->tile_offset, s->raw);
        dr0_finish_unstuff<<<1, 1, 0, c->stream>>>(s->tile_count, s->tile_offset, tiles, s->raw_size);
        dr0_speculate<<<(chunks + 127) / 128, 128, 0, c->stream>>>(s->raw, s->raw_size,
            h->d_tables_full, h->d_tables_quick, layout, chunks, s->boundary, s->counts, s->zero_chunks);
        for (int width = 2; width / 2 < chunks; width *= 2) {
            const int groups = (chunks + width - 1) / width;
            dr0_reconcile<<<(groups + 127) / 128, 128, 0, c->stream>>>(s->raw, s->raw_size,
                h->d_tables_full, h->d_tables_quick, layout, chunks, width, s->boundary, s->counts, s->zero_chunks);
        }
        dr0_scan<uint64_t, false>(s->counts, s->offsets, chunks, (uint8_t *)s->workspace, c->stream);
        dr0_check_count<<<1, 1, 0, c->stream>>>(s->counts, s->offsets, chunks, layout.blocks, s->error);
        dr0_write<<<(chunks + 127) / 128, 128, 0, c->stream>>>(s->raw, s->raw_size, h->d_tables_full,
            h->d_tables_quick, h->d_order_natural, layout, chunks, s->boundary, s->offsets,
            c->d_block_list, c->d_data_quantized, s->dc, s->error, s->zero_chunks);
        for (int comp = first_comp; comp < last_comp; ++comp)
            dr0_scan<int64_t, true>(s->dc + dc_base[comp], s->dc_sum + dc_base[comp], dc_count[comp], (uint8_t *)s->workspace, c->stream);
        dr0_restore_dc<<<(layout.blocks + 255) / 256, 256, 0, c->stream>>>(c->d_block_list, layout,
            s->dc_sum, c->d_data_quantized, s->error);
        gpujpeg_cuda_check_error("Restart-free speculative decoding", return -1);
    }
    unsigned int error = 0;
    if (cudaMemcpyAsync(&error, s->error, 4, cudaMemcpyDeviceToHost, c->stream) != cudaSuccess ||
        cudaStreamSynchronize(c->stream) != cudaSuccess) return -1;
    if (error) fprintf(stderr, "[GPUJPEG] Restart-free decode validation failed (%u)\n", error);
    return error ? -1 : 0;
#else
    (void)decoder; return -1;
#endif
}
