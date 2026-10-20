// Independent regression harness: public decoder output plus internal int16 DCT
// coefficients. Compile against each library from the SAME source revision.
// Usage: decoder_dump OUTPUT_DIRECTORY single|dual JPEG [JPEG ...]
#include <cuda_runtime.h>
#include "libgpujpeg/gpujpeg_decoder.h"
#include "src/gpujpeg_decoder_internal.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <vector>
#include <string>
#include <thread>
#include <atomic>
#include <exception>
#include <stdexcept>

static void cuda_check(cudaError_t result) {
    if (result != cudaSuccess) throw std::runtime_error(cudaGetErrorString(result));
}
static std::vector<unsigned char> read_file(const std::string& path) {
    std::ifstream file(path, std::ios::binary | std::ios::ate);
    if (!file) throw std::runtime_error("Cannot read " + path);
    std::vector<unsigned char> data((size_t) file.tellg());
    file.seekg(0); file.read((char*) data.data(), data.size());
    if (!file) throw std::runtime_error("Read failed " + path);
    return data;
}
static void write_file(const std::string& path, const void* data, size_t bytes) {
    std::ofstream file(path, std::ios::binary);
    file.write((const char*) data, bytes);
    if (!file) throw std::runtime_error("Cannot write " + path);
}

static void worker(int id, const std::string& directory,
                   const std::vector<std::string>& paths, std::atomic<int>& failure) {
    cudaStream_t stream = nullptr;
    gpujpeg_decoder* decoder = nullptr;
    try {
        cuda_check(cudaSetDevice(0));
        cuda_check(cudaStreamCreateWithFlags(&stream, cudaStreamNonBlocking));
        gpujpeg_decoder_init_parameters parameters = gpujpeg_decoder_default_init_parameters();
        parameters.stream = stream;
        parameters.verbose = GPUJPEG_LL_QUIET;
        parameters.perf_stats = false;
        decoder = gpujpeg_decoder_create_with_params(&parameters);
        if (!decoder) throw std::runtime_error("Decoder creation failed");
        // Two passes reuse each instance through changes in dimensions, tables,
        // subsampling and restart geometry. Worker 1 traverses the reverse order.
        for (int pass = 0; pass < 2; ++pass) {
            for (size_t step = 0; step < paths.size(); ++step) {
                const size_t index = id ? paths.size() - 1 - step : step;
                fprintf(stderr, "Worker %d pass %d input %zu %s\n", id, pass, index, paths[index].c_str());
                std::vector<unsigned char> jpeg = read_file(paths[index]);
                gpujpeg_decoder_output output;
                gpujpeg_decoder_output_set_default(&output);
                if (gpujpeg_decoder_decode(decoder, jpeg.data(), jpeg.size(), &output) != 0)
                    throw std::runtime_error("Decode failed " + paths[index]);
                if (pass == 0) continue;
                gpujpeg_coder& coder = decoder->coder;
                std::vector<int16_t> raw(coder.data_size);
                cuda_check(cudaMemcpyAsync(raw.data(), coder.d_data_quantized,
                           raw.size() * sizeof(int16_t), cudaMemcpyDeviceToHost, stream));
                cuda_check(cudaStreamSynchronize(stream));
                // The block list owns scan/coding order. Do not hash allocator
                // padding or unused component-buffer tails.
                std::vector<int16_t> coefficients((size_t) coder.block_count * 64);
                for (int block = 0; block < coder.block_count; ++block) {
                    const size_t offset = coder.block_list[block] >> 8;
                    if (offset + 64 > raw.size()) throw std::runtime_error("Bad block list");
                    memcpy(coefficients.data() + (size_t) block * 64, raw.data() + offset, 128);
                }
                char name[80];
                snprintf(name, sizeof name, "/w%d-%03zu", id, index);
                const std::string base = directory + name;
                write_file(base + ".pixels", output.data, output.data_size);
                write_file(base + ".coefficients", coefficients.data(), coefficients.size() * sizeof(int16_t));
                std::ofstream meta(base + ".json");
                meta << "{\"index\":" << index << ",\"worker\":" << id
                     << ",\"width\":" << output.param_image.width
                     << ",\"height\":" << output.param_image.height
                     << ",\"components\":" << coder.param.comp_count
                     << ",\"segments\":" << decoder->segment_count
                     << ",\"blocks\":" << coder.block_count
                     << ",\"restart_interval\":" << coder.param.restart_interval
                     << ",\"interleaved\":" << coder.param.interleaved
                     << ",\"pixel_format\":" << output.param_image.pixel_format
                     << ",\"color_space\":" << output.param_image.color_space
                     << ",\"pixel_bytes\":" << output.data_size
                     << ",\"coefficient_bytes\":" << coefficients.size() * 2 << "}\n";
            }
        }
    } catch (const std::exception& error) {
        fprintf(stderr, "Worker %d: %s\n", id, error.what());
        failure = 1;
    }
    if (decoder) gpujpeg_decoder_destroy(decoder);
    if (stream) cudaStreamDestroy(stream);
}

int main(int argc, char** argv) {
    if (argc < 4 || (strcmp(argv[2], "single") && strcmp(argv[2], "dual"))) {
        fprintf(stderr, "Usage: %s OUTPUT_DIRECTORY single|dual JPEG [JPEG ...]\n", argv[0]);
        return 2;
    }
    if (gpujpeg_init_device(0, 0) != 0) return 3;
    std::vector<std::string> paths;
    for (int i = 3; i < argc; ++i) paths.emplace_back(argv[i]);
    std::atomic<int> failure{0};
    const std::string directory = argv[1];
    if (!strcmp(argv[2], "single")) {
        worker(0, argv[1], paths, failure);
    } else {
        std::thread first(worker, 0, std::cref(directory), std::cref(paths), std::ref(failure));
        std::thread second(worker, 1, std::cref(directory), std::cref(paths), std::ref(failure));
        first.join(); second.join();
    }
    return failure.load();
}
