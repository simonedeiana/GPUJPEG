// Sequential public-API encoder reconfiguration; outputs each call for byte comparison.
#define main single_benchmark_main
#include "gpujpeg_encode_bench.cu"
#undef main
#include <sstream>
#include <thread>
#include <atomic>
#include <mutex>
struct StateCase {
    std::string name,raw,sampling;
    int width,height,channels,quality,interleaved,restart;
    std::vector<unsigned char> pixels;
};
int main(int argc,char** argv) {
    cudaStream_t stream=nullptr;gpujpeg_encoder* encoder=nullptr;
    try {
        if(argc!=3 && argc!=4) throw std::runtime_error("Usage: encoder_state_check CASES.tsv OUTPUT_DIRECTORY [--concurrent]");
        std::ifstream manifest(argv[1]);std::string line;std::vector<StateCase> cases;
        while(std::getline(manifest,line)) {
            if(line.empty())continue;
            std::istringstream row(line);StateCase c;
            if(!(row>>c.name>>std::quoted(c.raw)>>c.width>>c.height>>c.channels>>c.sampling
                 >>c.quality>>c.interleaved>>c.restart))throw std::runtime_error("Bad state manifest row");
            c.pixels.resize((size_t)c.width*c.height*c.channels);
            std::ifstream input(c.raw,std::ios::binary|std::ios::ate);
            if(!input || (size_t)input.tellg()!=c.pixels.size())throw std::runtime_error("Wrong raw size");
            input.seekg(0);input.read((char*)c.pixels.data(),c.pixels.size());
            if(!input)throw std::runtime_error("Raw read failed");cases.push_back(std::move(c));
        }
        if(cases.empty())throw std::runtime_error("No state cases");
        if(gpujpeg_init_device(0,0))throw std::runtime_error("Device init failed");
        check(cudaStreamCreateWithFlags(&stream,cudaStreamNonBlocking));
        encoder=gpujpeg_encoder_create(stream);if(!encoder)throw std::runtime_error("Encoder creation failed");
        auto encode_case=[&](const StateCase& c,gpujpeg_encoder* active,cudaStream_t active_stream,int index,int repeats) {
            auto p=gpujpeg_default_parameters();p.quality=c.quality;p.restart_interval=c.restart;
            p.interleaved=c.interleaved;p.verbose=GPUJPEG_LL_QUIET;p.perf_stats=false;
            gpujpeg_parameters_chroma_subsampling(&p,c.channels==1?GPUJPEG_SUBSAMPLING_400:
                gpujpeg_subsampling_from_name(c.sampling.c_str()));
            auto im=gpujpeg_default_image_parameters();im.width=c.width;im.height=c.height;
            im.pixel_format=c.channels==1?GPUJPEG_U8:GPUJPEG_444_U8_P012;
            im.color_space=c.channels==1?GPUJPEG_YCBCR_BT601_256LVLS:GPUJPEG_RGB;
            auto input=gpujpeg_encoder_input_image(const_cast<unsigned char*>(c.pixels.data()));
            // Repeat each configuration to distinguish reinitialization and stable-state results.
            for(int repeat=0;repeat<repeats;++repeat) {
                unsigned char* data=nullptr;size_t bytes=0;
                if(gpujpeg_encoder_encode(active,&p,&im,&input,&data,&bytes))throw std::runtime_error("Encode failed");
                check(cudaStreamSynchronize(active_stream));
                std::ostringstream name;name<<argv[2]<<"/"<<std::setw(3)<<std::setfill('0')<<index<<"-"<<c.name<<"-"<<repeat<<".jpg";
                std::ofstream output(name.str(),std::ios::binary);output.write((char*)data,bytes);
                if(!output)throw std::runtime_error("Output write failed");
            }
        };
        if(argc==4) {
            if(std::string(argv[3])!="--concurrent" || cases.size()!=2)throw std::runtime_error("Concurrent mode requires exactly two cases");
            cudaStream_t stream2=nullptr;check(cudaStreamCreateWithFlags(&stream2,cudaStreamNonBlocking));
            auto encoder2=gpujpeg_encoder_create(stream2);if(!encoder2)throw std::runtime_error("Second encoder creation failed");
            // Allocate both states sequentially before overlap. No library global initialization in threads.
            encode_case(cases[0],encoder,stream,0,1);encode_case(cases[1],encoder2,stream2,1,1);
            check(cudaDeviceSynchronize());
            std::atomic<int> ready{0};std::exception_ptr failure;std::mutex failure_mutex;
            auto worker=[&](int slot,gpujpeg_encoder* active,cudaStream_t active_stream) {
                try {check(cudaSetDevice(0));ready.fetch_add(1);while(ready.load()!=2)std::this_thread::yield();
                    encode_case(cases[slot],active,active_stream,slot,5);
                }catch(...) {std::lock_guard<std::mutex> lock(failure_mutex);failure=std::current_exception();}
            };
            std::thread first(worker,0,encoder,stream),second(worker,1,encoder2,stream2);first.join();second.join();
            gpujpeg_encoder_destroy(encoder2);check(cudaStreamDestroy(stream2));if(failure)std::rethrow_exception(failure);
        } else {
            int index=0;for(const auto& c:cases)encode_case(c,encoder,stream,index++,2);
        }
        gpujpeg_encoder_destroy(encoder);encoder=nullptr;check(cudaStreamDestroy(stream));stream=nullptr;
        return 0;
    }catch(const std::exception& error){std::cerr<<error.what()<<"\n";
        if(encoder)gpujpeg_encoder_destroy(encoder);if(stream)cudaStreamDestroy(stream);return 1;}
}
