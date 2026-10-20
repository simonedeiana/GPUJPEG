#include <windows.h>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cupti.h>

static CUpti_SubscriberHandle subscriber;
static SRWLOCK lock = SRWLOCK_INIT;
static FILE* logFile;
static char outputDir[MAX_PATH * 4];
static unsigned seq;

static void captureCopy(const char* api, const void* src, size_t bytes, int kind) {
    if (kind != cudaMemcpyHostToDevice) return;
    fprintf(logFile, "copy\t%s\t%zu\t%p\n", api, bytes, src);
    if (bytes == 0 || bytes > 2 * 1024 * 1024) return;
    char name[MAX_PATH * 4];
    sprintf_s(name, "%s\\h2d-%03u-%zu.bin", outputDir, seq++, bytes);
    FILE* f; fopen_s(&f, name, "wb");
    if (f) { fwrite(src, 1, bytes, f); fclose(f); }
}

static void CUPTIAPI callback(void*, CUpti_CallbackDomain domain, CUpti_CallbackId, const void* info) {
    if (domain != CUPTI_CB_DOMAIN_RUNTIME_API) return;
    const CUpti_CallbackData* cb = (const CUpti_CallbackData*) info;
    if (cb->callbackSite != CUPTI_API_ENTER || !logFile) return;
    AcquireSRWLockExclusive(&lock);
    const char* name = cb->functionName;
    if (!strcmp(name,"cudaMemcpy") || !strcmp(name,"cudaMemcpy_ptds")) {
        const auto* p = (const cudaMemcpy_v3020_params*) cb->functionParams;
        captureCopy(name,p->src,p->count,p->kind);
    } else if (!strcmp(name,"cudaMemcpyAsync") || !strcmp(name,"cudaMemcpyAsync_ptsz")) {
        const auto* p = (const cudaMemcpyAsync_v3020_params*) cb->functionParams;
        captureCopy(name,p->src,p->count,p->kind);
    } else if (!strcmp(name,"cudaLaunchKernel") || !strcmp(name,"cudaLaunchKernel_ptsz")) {
        const auto* p = (const cudaLaunchKernel_v7000_params*) cb->functionParams;
        fprintf(logFile,"kernel\t%s\t%u,%u,%u\t%u,%u,%u\n",cb->symbolName ? cb->symbolName : "?",
                p->gridDim.x,p->gridDim.y,p->gridDim.z,p->blockDim.x,p->blockDim.y,p->blockDim.z);
        if (cb->symbolName && strstr(cb->symbolName,"HuffmanDecodeKernel")) {
            for (unsigned i=0;i<11;i++) {
                fprintf(logFile,"arg\t%u\t",i);
                unsigned size=i<5 ? 8 : i<10 ? 4 : 16;
                for (unsigned j=0;j<size;j++) fprintf(logFile,"%02x",((const unsigned char*)p->args[i])[j]);
                fprintf(logFile,"\n");
            }
        }
    }
    fflush(logFile);
    ReleaseSRWLockExclusive(&lock);
}

extern "C" __declspec(dllexport) int InitializeInjection() {
    DWORD n=GetEnvironmentVariableA("FASTJPEG_CAPTURE_DIR",outputDir,sizeof(outputDir));
    if (!n || n>=sizeof(outputDir)) return 0;
    char name[MAX_PATH*4]; sprintf_s(name,"%s\\capture.tsv",outputDir);
    fopen_s(&logFile,name,"wb");
    if (!logFile) return 0;
    CUptiResult result=cuptiSubscribe(&subscriber,callback,nullptr);
    fprintf(logFile,"subscribe\t%d\n",(int)result);
    if (result == CUPTI_SUCCESS) result=cuptiEnableDomain(1,subscriber,CUPTI_CB_DOMAIN_RUNTIME_API);
    fprintf(logFile,"enable\t%d\n",(int)result); fflush(logFile);
    return result == CUPTI_SUCCESS;
}
