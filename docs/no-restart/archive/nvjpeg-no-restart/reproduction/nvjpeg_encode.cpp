// Independent public-API harness. No recovered NVIDIA implementation is linked
// into GPUJPEG; this executable measures the installed vendor library itself.
#include <cuda_runtime_api.h>
#include <nvjpeg.h>
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <map>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>

static void ck(cudaError_t s) { if(s!=cudaSuccess) throw std::runtime_error(cudaGetErrorString(s)); }
static void ck(nvjpegStatus_t s) { if(s!=NVJPEG_STATUS_SUCCESS) throw std::runtime_error("nvJPEG status "+std::to_string((int)s)); }
static double median(std::vector<double> a) { std::sort(a.begin(),a.end()); return (a[(a.size()-1)/2]+a[a.size()/2])/2; }
struct Audit { int restartMarkers=0; std::vector<int> dri; size_t entropyOffset=0; };
static Audit audit(const std::vector<unsigned char>& b,size_t size) {
    if(size<4 || b[0]!=255 || b[1]!=216) throw std::runtime_error("Missing JPEG SOI");
    Audit a; size_t p=2;
    while(p+4<=size) {
        if(b[p]!=255) throw std::runtime_error("Invalid JPEG header");
        int marker=b[p+1],len=(b[p+2]<<8)|b[p+3];
        if(len<2 || p+2+len>size) throw std::runtime_error("Invalid JPEG segment length");
        if(marker==221) { if(len!=4) throw std::runtime_error("Invalid DRI"); a.dri.push_back((b[p+4]<<8)|b[p+5]); }
        p+=2+len; if(marker==218) { a.entropyOffset=p; break; }
    }
    if(!a.entropyOffset) throw std::runtime_error("Missing SOS");
    bool eoi=false;
    while(p+1<size) {
        if(b[p++]!=255) continue;
        int marker=b[p++]; if(marker==0) continue;
        if(marker>=208 && marker<=215) { a.restartMarkers++; continue; }
        if(marker==217) { eoi=true; break; }
        if(marker==255) { --p; continue; }
        throw std::runtime_error("Unexpected marker in baseline scan");
    }
    if(!eoi || a.restartMarkers || std::any_of(a.dri.begin(),a.dri.end(),[](int x){return x!=0;}))
        throw std::runtime_error("Output is not complete restart-free JPEG");
    return a;
}
int main(int argc,char** argv) try {
    std::map<std::string,std::string> options;
    for(int i=1;i<argc;i+=2) { if(i+1==argc) throw std::runtime_error("Expected --name value"); options[argv[i]]=argv[i+1]; }
    auto get=[&](const char* key,const char* def)->std::string {auto i=options.find(key); return i==options.end()?def:i->second;};
    int width=std::stoi(get("--width","0")),height=std::stoi(get("--height","0")),channels=std::stoi(get("--channels","3"));
    int iterations=std::stoi(get("--iterations","110")),warmup=std::stoi(get("--warmup","10")),quality=std::stoi(get("--quality","90"));
    int optimized=std::stoi(get("--optimized","0")),pinned=std::stoi(get("--pinned","0"));
    std::string sampling=get("--sampling",channels==1?"gray":"444");
    if(width<=0 || height<=0 || (channels!=1 && channels!=3) || iterations<=warmup || warmup<0 || quality<1 || quality>100 || (optimized!=0 && optimized!=1) || (pinned!=0 && pinned!=1)) throw std::runtime_error("Invalid dimensions/settings");
    nvjpegChromaSubsampling_t css;
    if(channels==1 && sampling=="gray") css=NVJPEG_CSS_GRAY;
    else if(channels==3 && sampling=="444") css=NVJPEG_CSS_444;
    else if(channels==3 && sampling=="422") css=NVJPEG_CSS_422;
    else if(channels==3 && sampling=="420") css=NVJPEG_CSS_420;
    else throw std::runtime_error("Sampling/channel mismatch");
    size_t bytes=(size_t)width*height*channels;
    std::ifstream input(get("--input",""),std::ios::binary|std::ios::ate);
    if(!input || (size_t)input.tellg()!=bytes) throw std::runtime_error("Raw input size mismatch");
    std::vector<unsigned char> host(bytes); input.seekg(0); input.read((char*)host.data(),bytes);
    unsigned char* source=host.data(); unsigned char* hostPinned=nullptr;
    if(pinned) { ck(cudaMallocHost((void**)&hostPinned,bytes)); std::copy(host.begin(),host.end(),hostPinned); source=hostPinned; }
    cudaStream_t stream; ck(cudaStreamCreateWithFlags(&stream,cudaStreamNonBlocking));
    nvjpegHandle_t handle; ck(nvjpegCreateSimple(&handle));
    nvjpegEncoderState_t state; ck(nvjpegEncoderStateCreateWithBackend(handle,&state,NVJPEG_ENC_BACKEND_GPU,stream));
    nvjpegEncoderParams_t params; ck(nvjpegEncoderParamsCreate(handle,&params,stream));
    ck(nvjpegEncoderParamsSetEncoding(params,NVJPEG_ENCODING_BASELINE_DCT,stream));
    ck(nvjpegEncoderParamsSetQuality(params,quality,stream));
    ck(nvjpegEncoderParamsSetSamplingFactors(params,css,stream));
    ck(nvjpegEncoderParamsSetOptimizedHuffman(params,optimized,stream));
    ck(nvjpegEncoderParamsSetRestartInterval(params,0,stream));
    size_t capacity=0; ck(nvjpegEncodeGetBufferSize(handle,params,width,height,&capacity));
    std::vector<unsigned char> compressed(capacity);
    unsigned char* device; ck(cudaMalloc((void**)&device,bytes));
    nvjpegImage_t image={}; image.channel[0]=device; image.pitch[0]=(size_t)width*channels;
    ck(cudaStreamSynchronize(stream));
    std::vector<double> times; size_t length=0;
    for(int i=0;i<iterations;i++) {
        auto begin=std::chrono::steady_clock::now();
        ck(cudaMemcpyAsync(device,source,bytes,cudaMemcpyHostToDevice,stream));
        if(channels==1) ck(nvjpegEncodeYUV(handle,state,params,&image,NVJPEG_CSS_GRAY,width,height,stream));
        else ck(nvjpegEncodeImage(handle,state,params,&image,NVJPEG_INPUT_RGBI,width,height,stream));
        length=capacity;
        ck(nvjpegEncodeRetrieveBitstream(handle,state,compressed.data(),&length,stream));
        ck(cudaStreamSynchronize(stream));
        auto end=std::chrono::steady_clock::now();
        times.push_back(std::chrono::duration<double,std::milli>(end-begin).count());
        if(length>capacity) throw std::runtime_error("Compressed output exceeded capacity");
    }
    Audit a=audit(compressed,length);
    std::ofstream output(get("--output","encoded.jpg"),std::ios::binary); output.write((const char*)compressed.data(),length); output.close();
    if(!output) throw std::runtime_error("Output write failed");
    std::vector<double> warm(times.begin()+warmup,times.end());
    printf("{\"codec\":\"nvJPEG\",\"backend\":\"GPU\",\"restart_interval_requested\":0,\"restart_marker_count\":%d,\"dri_values\":[",a.restartMarkers);
    for(size_t i=0;i<a.dri.size();i++) printf("%s%d",i?",":"",a.dri[i]);
    printf("],\"width\":%d,\"height\":%d,\"channels\":%d,\"sampling\":\"%s\",\"quality\":%d,\"optimized\":%d,\"pinned\":%d,\"input_bytes\":%zu,\"output_bytes\":%zu,\"entropy_offset\":%zu,\"iterations\":%d,\"warmup\":%d,\"primary_ms\":[",width,height,channels,sampling.c_str(),quality,optimized,pinned,bytes,length,a.entropyOffset,iterations,warmup);
    for(size_t i=0;i<times.size();i++) printf("%s%.9f",i?",":"",times[i]);
    printf("],\"warm_primary\":{\"count\":%zu,\"median_ms\":%.9f,\"mean_ms\":%.9f,\"min_ms\":%.9f,\"max_ms\":%.9f},\"boundary\":\"steady_clock immediately before raw H2D through nvjpegEncode, host bitstream retrieval and stream synchronization; file IO and setup excluded\"}\n",warm.size(),median(warm),std::accumulate(warm.begin(),warm.end(),0.0)/warm.size(),*std::min_element(warm.begin(),warm.end()),*std::max_element(warm.begin(),warm.end()));
    ck(cudaFree(device)); ck(nvjpegEncoderParamsDestroy(params)); ck(nvjpegEncoderStateDestroy(state)); ck(nvjpegDestroy(handle)); ck(cudaStreamDestroy(stream)); if(hostPinned) ck(cudaFreeHost(hostPinned));
    return 0;
} catch(const std::exception& e) { fprintf(stderr,"%s\n",e.what()); return 1; }
