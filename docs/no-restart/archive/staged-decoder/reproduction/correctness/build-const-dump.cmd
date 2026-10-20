@echo off
call "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat"
nvcc -O2 -std=c++17 -Xcompiler /MD -Xcompiler /Zc:__cplusplus -DGPUJPEG_STATIC -Iwork/GPUJPEG -Iwork/build-staged-const work/new-decode-benchmark/correctness/decoder_dump.cu work/build-staged-const/gpujpeg.lib -o work/new-decode-benchmark/correctness/dump-staged-const.exe
