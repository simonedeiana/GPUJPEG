@echo off
call "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat"
set "TASK_CORRECT=work/new-decode-benchmark/correctness"
nvcc -O2 -std=c++17 -Xcompiler /MD -Xcompiler /Zc:__cplusplus -DGPUJPEG_STATIC -Iwork/GPUJPEG -Iwork/build-baseline "%TASK_CORRECT%/decoder_dump.cu" work/build-baseline/gpujpeg.lib -o "%TASK_CORRECT%/dump-baseline.exe"
if errorlevel 1 exit /b %errorlevel%
nvcc -O2 -std=c++17 -Xcompiler /MD -Xcompiler /Zc:__cplusplus -DGPUJPEG_STATIC -Iwork/GPUJPEG -Iwork/build-staged "%TASK_CORRECT%/decoder_dump.cu" work/build-staged/gpujpeg.lib -o "%TASK_CORRECT%/dump-staged.exe"
