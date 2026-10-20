// Whole-call GPUJPEG encoder benchmark. Public API only; no stage timers.
#include <cuda_runtime.h>
#include "libgpujpeg/gpujpeg_encoder.h"
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>

static void check(cudaError_t result) {
    if (result != cudaSuccess) throw std::runtime_error(cudaGetErrorString(result));
}
static double milliseconds(std::chrono::steady_clock::time_point start) {
    return std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
}
int main(int argc,char** argv) {
    cudaStream_t stream=nullptr;
    gpujpeg_encoder* encoder=nullptr;
    unsigned char* pinned_input=nullptr;
    try {
        std::map<std::string,std::string> options;
        for (int i=1;i<argc;i+=2) {
            if (i+1>=argc) throw std::runtime_error("Expected --option value pairs");
            options[argv[i]]=argv[i+1];
        }
        auto get=[&](const char* name,const char* fallback="") {
            auto found=options.find(name);return found==options.end()?std::string(fallback):found->second;
        };
        const int width=std::stoi(get("--width")),height=std::stoi(get("--height"));
        const int channels=std::stoi(get("--channels"));
        const int quality=std::stoi(get("--quality","90"));
        const int iterations=std::stoi(get("--iterations","110"));
        const int warmup=std::stoi(get("--warmup","10"));
        const bool pinned=std::stoi(get("--pinned","0"))!=0;
        const int interleaved=std::stoi(get("--interleaved","1"));
        const std::string sampling=get("--sampling",channels==1?"gray":"444");
        if (width<=0 || height<=0 || width>65535 || height>65535 || (channels!=1 && channels!=3)
            || quality<0 || quality>100 || iterations<=warmup || warmup<0
            || (interleaved!=0 && interleaved!=1) || get("--optimized","0")!="0")
            throw std::runtime_error("Invalid dimensions, quality, iteration count or unsupported optimized Huffman setting");
        if ((channels==1)!=(sampling=="gray")) throw std::runtime_error("Sampling/channel mismatch");
        const size_t input_bytes=(size_t)width*height*channels;
        std::ifstream input(get("--input"),std::ios::binary|std::ios::ate);
        if (!input || (size_t)input.tellg()!=input_bytes) throw std::runtime_error("Wrong raw input byte count");
        std::vector<unsigned char> host(input_bytes);input.seekg(0);
        input.read((char*)host.data(),host.size());
        if (!input) throw std::runtime_error("Raw input read failed");

        const auto setup_start=std::chrono::steady_clock::now();
        if (gpujpeg_init_device(0,0)!=0) throw std::runtime_error("Device initialization failed");
        check(cudaStreamCreateWithFlags(&stream,cudaStreamNonBlocking));
        encoder=gpujpeg_encoder_create(stream);
        if (!encoder) throw std::runtime_error("Encoder creation failed");
        if (pinned) {
            check(cudaMallocHost((void**)&pinned_input,input_bytes));
            std::memcpy(pinned_input,host.data(),input_bytes);
            if (gpujpeg_encoder_set_option(encoder,GPUJPEG_ENC_OPT_OUT,GPUJPEG_ENC_OUT_VAL_PINNED)!=0)
                throw std::runtime_error("Pinned output selection failed");
        }
        gpujpeg_parameters parameters=gpujpeg_default_parameters();
        parameters.quality=quality;parameters.restart_interval=0;parameters.interleaved=interleaved;
        parameters.verbose=GPUJPEG_LL_QUIET;parameters.perf_stats=false;
        const auto subsampling=channels==1?GPUJPEG_SUBSAMPLING_400:gpujpeg_subsampling_from_name(sampling.c_str());
        if (subsampling==GPUJPEG_SUBSAMPLING_UNKNOWN) throw std::runtime_error("Unknown sampling format");
        gpujpeg_parameters_chroma_subsampling(&parameters,subsampling);
        gpujpeg_image_parameters image=gpujpeg_default_image_parameters();
        image.width=width;image.height=height;
        image.pixel_format=channels==1?GPUJPEG_U8:GPUJPEG_444_U8_P012;
        image.color_space=channels==1?GPUJPEG_YCBCR_BT601_256LVLS:GPUJPEG_RGB;
        gpujpeg_encoder_input source=gpujpeg_encoder_input_image(pinned?pinned_input:host.data());
        const double setup_ms=milliseconds(setup_start);
        std::vector<double> primary;
        unsigned char* jpeg=nullptr;size_t jpeg_bytes=0;
        for (int frame=0;frame<iterations;++frame) {
            check(cudaStreamSynchronize(stream));
            const auto start=std::chrono::steady_clock::now();
            const int result=gpujpeg_encoder_encode(encoder,&parameters,&image,&source,&jpeg,&jpeg_bytes);
            check(cudaStreamSynchronize(stream));
            primary.push_back(milliseconds(start));
            if (result!=0 || !jpeg || !jpeg_bytes) throw std::runtime_error("Encoding failed");
        }
        const std::string output=get("--output");
        if (!output.empty()) {
            std::ofstream file(output,std::ios::binary);file.write((char*)jpeg,jpeg_bytes);
            if (!file) throw std::runtime_error("JPEG output write failed");
        }
        std::vector<double> warm(primary.begin()+warmup,primary.end());
        std::sort(warm.begin(),warm.end());
        const size_t n=warm.size();
        const double median=n%2?warm[n/2]:(warm[n/2-1]+warm[n/2])/2;
        const double mean=std::accumulate(warm.begin(),warm.end(),0.0)/n;
        std::cout<<std::setprecision(12)<<"{\"library\":\"GPUJPEG\",\"restart_interval_requested\":0"
            <<",\"width\":"<<width<<",\"height\":"<<height<<",\"channels\":"<<channels
            <<",\"sampling\":"<<std::quoted(sampling)<<",\"interleaved\":"<<interleaved
            <<",\"quality\":"<<quality<<",\"optimized\":0,\"pinned\":"<<(pinned?1:0)
            <<",\"input_bytes\":"<<input_bytes<<",\"output_bytes\":"<<jpeg_bytes
            <<",\"iterations\":"<<iterations<<",\"warmup\":"<<warmup<<",\"setup_ms\":"<<setup_ms
            <<",\"primary_ms\":[";
        for(size_t i=0;i<primary.size();++i){if(i)std::cout<<',';std::cout<<primary[i];}
        std::cout<<"],\"warm_primary\":{\"count\":"<<n<<",\"median_ms\":"<<median
            <<",\"mean_ms\":"<<mean<<",\"min_ms\":"<<warm.front()<<",\"max_ms\":"<<warm.back()<<"}}\n";
        gpujpeg_encoder_destroy(encoder);encoder=nullptr;
        if(pinned_input){check(cudaFreeHost(pinned_input));pinned_input=nullptr;}
        check(cudaStreamDestroy(stream));stream=nullptr;
        return 0;
    }catch(const std::exception& error){
        std::cerr<<error.what()<<'\n';
        if(encoder)gpujpeg_encoder_destroy(encoder);
        if(pinned_input)cudaFreeHost(pinned_input);
        if(stream)cudaStreamDestroy(stream);
        return 1;
    }
}
