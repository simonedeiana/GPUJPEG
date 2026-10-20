#include <cuda_runtime.h>
#include "libgpujpeg/gpujpeg_decoder.h"
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

static void ck(cudaError_t e) { if (e != cudaSuccess) throw std::runtime_error(cudaGetErrorString(e)); }
int main(int argc, char **argv) try {
    if (argc != 5) throw std::runtime_error("decode_bench JPEG ITERATIONS WARMUP OUTPUT_RAW");
    const int iterations = std::stoi(argv[2]), warmup = std::stoi(argv[3]);
    if (iterations <= warmup || warmup < 0) throw std::runtime_error("Invalid sample counts");
    std::ifstream f(argv[1], std::ios::binary | std::ios::ate);
    if (!f) throw std::runtime_error("Cannot open JPEG");
    std::vector<unsigned char> jpeg((size_t)f.tellg()); f.seekg(0); f.read((char *)jpeg.data(), jpeg.size());
    if (!f) throw std::runtime_error("Cannot read JPEG");
    ck(cudaSetDevice(0)); cudaStream_t stream; ck(cudaStreamCreateWithFlags(&stream, cudaStreamNonBlocking));
    auto parameters = gpujpeg_decoder_default_init_parameters();
    parameters.stream = stream; parameters.verbose = GPUJPEG_LL_QUIET; parameters.perf_stats = false;
    auto *decoder = gpujpeg_decoder_create_with_params(&parameters);
    if (!decoder) throw std::runtime_error("Decoder creation failed");
    std::vector<double> times; gpujpeg_decoder_output output;
    for (int i = 0; i < iterations; ++i) {
        gpujpeg_decoder_output_set_default(&output);
        ck(cudaStreamSynchronize(stream));
        auto start = std::chrono::steady_clock::now();
        const int result = gpujpeg_decoder_decode(decoder, jpeg.data(), jpeg.size(), &output);
        ck(cudaStreamSynchronize(stream));
        auto stop = std::chrono::steady_clock::now();
        if (result) throw std::runtime_error("Decode failed");
        times.push_back(std::chrono::duration<double, std::milli>(stop - start).count());
    }
    std::ofstream out(argv[4], std::ios::binary); out.write((char *)output.data, output.data_size);
    if (!out) throw std::runtime_error("Raw output write failed");
    std::vector<double> warm(times.begin() + warmup, times.end()); std::sort(warm.begin(), warm.end());
    const double median = (warm[(warm.size() - 1) / 2] + warm[warm.size() / 2]) / 2;
    printf("{\"width\":%d,\"height\":%d,\"output_bytes\":%zu,\"input_bytes\":%zu,\"iterations\":%d,\"warmup\":%d,\"median_ms\":%.9f,\"samples_ms\":[",
        output.param_image.width, output.param_image.height, output.data_size, jpeg.size(), iterations, warmup, median);
    for (size_t i = 0; i < times.size(); ++i) printf("%s%.9f", i ? "," : "", times[i]);
    printf("]}\n");
    gpujpeg_decoder_destroy(decoder); ck(cudaStreamDestroy(stream));
    return 0;
} catch (const std::exception &e) { fprintf(stderr, "%s\n", e.what()); return 1; }
