@echo off
call "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat"
set "TASK_ENCODE_CHECK=work/encode-r0-validation"
nvcc -O2 -std=c++17 -Xcompiler /MD -Xcompiler /Zc:__cplusplus -DGPUJPEG_STATIC -Iwork/GPUJPEG -Iwork/build-decoder-r0 work/encode-r0-validation/decoder_dump.cu work/build-decoder-r0/gpujpeg.lib -o work/decoder-r0/dump-candidate.exe
if errorlevel 1 exit /b %errorlevel%
nvcc -O2 -std=c++17 -Xcompiler /MD -Xcompiler /Zc:__cplusplus -DGPUJPEG_STATIC -Iwork/GPUJPEG -Iwork/build-decoder-r0 -c work/decoder-r0/decode_bench.cu -o work/decoder-r0/decode_bench.obj
if errorlevel 1 exit /b %errorlevel%
nvcc -Xcompiler /MD work/decoder-r0/decode_bench.obj work/build-encode-r0/gpujpeg.lib -o work/decoder-r0/bench-locked.exe
if errorlevel 1 exit /b %errorlevel%
nvcc -Xcompiler /MD work/decoder-r0/decode_bench.obj work/build-decoder-r0/gpujpeg.lib -o work/decoder-r0/bench-candidate.exe
