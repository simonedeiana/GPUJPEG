// Scoped, version-specific observation of Fastvideo's existing host timers.
// No CUDA APIs or CUPTI callbacks are enabled; CUDA's documented injection
// entry point only installs an IAT hook in this explicitly launched process.
#include <windows.h>
#include <intrin.h>
#include <cstdio>
#include <cstring>
#include <cstdint>

using QpcFunction=BOOL (WINAPI*)(LARGE_INTEGER*);
static QpcFunction originalQpc;
static uintptr_t imageBase;
static LARGE_INTEGER* loadStart;
static LARGE_INTEGER* decodeStart;
static char outputPath[4096];
struct Record { char kind; DWORD thread; long long start,stop,frequency; };
static Record records[16384];
static volatile LONG recordCount;

static void recordElapsed(char kind, LARGE_INTEGER* counter) {
    const LONG index=InterlockedIncrement(&recordCount)-1;
    if (index<(LONG)(sizeof(records)/sizeof(records[0]))) {
        records[index]={kind,GetCurrentThreadId(),counter[-1].QuadPart,counter[0].QuadPart,counter[-2].QuadPart};
    }
}

static __declspec(noinline) BOOL WINAPI observeQpc(LARGE_INTEGER* counter) {
    const uintptr_t returnRva=(uintptr_t)_ReturnAddress()-imageBase;
    const BOOL result=originalQpc(counter);
    if (!result) return result;
    if (returnRva==0x7211) loadStart=counter;
    if (returnRva==0x73da) decodeStart=counter;
    if (returnRva==0x6bae7 && ((loadStart && counter==loadStart+1) || (decodeStart && counter==decodeStart+1))) {
        recordElapsed(counter==loadStart+1 ? 'L' : 'D',counter);
    }
    return result;
}

extern "C" __declspec(dllexport) int InitializeInjection() {
    if (originalQpc) return 1;
    DWORD n=GetEnvironmentVariableA("FASTJPEG_QPC_LOG",outputPath,sizeof(outputPath));
    if (!n || n>=sizeof(outputPath)) return 0;
    imageBase=(uintptr_t)GetModuleHandleW(nullptr);
    const auto* dos=(const IMAGE_DOS_HEADER*)imageBase;
    const auto* nt=(const IMAGE_NT_HEADERS64*)(imageBase+dos->e_lfanew);
    const auto* imports=(const IMAGE_IMPORT_DESCRIPTOR*)(imageBase+nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress);
    for (;imports->Name;imports++) {
        if (!imports->OriginalFirstThunk) continue;
        const auto* names=(const IMAGE_THUNK_DATA64*)(imageBase+imports->OriginalFirstThunk);
        auto* entries=(IMAGE_THUNK_DATA64*)(imageBase+imports->FirstThunk);
        for (;names->u1.AddressOfData;names++,entries++) {
            if (IMAGE_SNAP_BY_ORDINAL64(names->u1.Ordinal)) continue;
            const auto* name=(const IMAGE_IMPORT_BY_NAME*)(imageBase+names->u1.AddressOfData);
            if (strcmp((const char*)name->Name,"QueryPerformanceCounter")) continue;
            DWORD previous;
            if (!VirtualProtect(&entries->u1.Function,sizeof(void*),PAGE_READWRITE,&previous)) return 0;
            originalQpc=(QpcFunction)(uintptr_t)entries->u1.Function;
            InterlockedExchangePointer((PVOID volatile*)&entries->u1.Function,(PVOID)&observeQpc);
            DWORD ignored;
            VirtualProtect(&entries->u1.Function,sizeof(void*),previous,&ignored);
            return 1;
        }
    }
    return 0;
}

static void dump() {
    if (!outputPath[0]) return;
    FILE* file;
    if (fopen_s(&file,outputPath,"wb") || !file) return;
    fprintf(file,"kind\tthread\tstart\tstop\tfrequency\telapsed_ms\n");
    const LONG count=recordCount<16384 ? recordCount : 16384;
    for (LONG i=0;i<count;i++) {
        const auto& r=records[i];
        fprintf(file,"%c\t%lu\t%lld\t%lld\t%lld\t%.9f\n",r.kind,(unsigned long)r.thread,r.start,r.stop,r.frequency,
                1000.0*(double)(r.stop-r.start)/(double)r.frequency);
    }
    fclose(file);
}

BOOL WINAPI DllMain(HINSTANCE, DWORD reason, LPVOID) {
    if (reason==DLL_PROCESS_DETACH) dump();
    return TRUE;
}

#ifdef QPC_CAPTURE_SELFTEST
int main() {
    originalQpc=(QpcFunction)GetProcAddress(GetModuleHandleW(L"kernel32.dll"),"QueryPerformanceCounter");
    LARGE_INTEGER frequency; QueryPerformanceFrequency(&frequency);
    printf("frequency=%lld\n",frequency.QuadPart);
    const int count=200000;
    for (int round=0;round<7;round++) {
        LARGE_INTEGER begin,end,counter;
        originalQpc(&begin);
        for (int i=0;i<count;i++) originalQpc(&counter);
        originalQpc(&end);
        const double baseline=1e9*(double)(end.QuadPart-begin.QuadPart)/(frequency.QuadPart*count);
        LARGE_INTEGER fake[3]; fake[0]=frequency; fake[1].QuadPart=0; fake[2].QuadPart=1;
        originalQpc(&begin);
        for (int i=0;i<count;i++) {
            observeQpc(&counter);
            // Record on EVERY simulated call, although only half of the
            // four actual per-frame calls record. This is conservative.
            if (i%16000==0) recordCount=0;
            recordElapsed('D',fake+2);
        }
        originalQpc(&end);
        const double observed=1e9*(double)(end.QuadPart-begin.QuadPart)/(frequency.QuadPart*count);
        printf("round=%d baseline_ns=%.3f observer_plus_record_ns=%.3f conservative_four_call_delta_us=%.6f\n",round,baseline,observed,4*(observed-baseline)/1000);
    }
    return 0;
}
#endif
