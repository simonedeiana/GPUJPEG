@echo off
call "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat"
set "TASK_ENCODE_CHECK=work/encode-r0-validation"
nvcc -O2 -std=c++17 -Xcompiler /MD -Xcompiler /Zc:__cplusplus -DGPUJPEG_STATIC -Iwork/GPUJPEG -Iwork/build-baseline -c "%TASK_ENCODE_CHECK%/gpujpeg_encode_bench.cu" -o "%TASK_ENCODE_CHECK%/gpujpeg_encode_bench.obj"
if errorlevel 1 exit /b %errorlevel%
nvcc -Xcompiler /MD "%TASK_ENCODE_CHECK%/gpujpeg_encode_bench.obj" work/build-baseline/gpujpeg.lib -o "%TASK_ENCODE_CHECK%/gpujpeg-baseline.exe"
if errorlevel 1 exit /b %errorlevel%
if "%~1"=="" exit /b 0
nvcc -Xcompiler /MD "%TASK_ENCODE_CHECK%/gpujpeg_encode_bench.obj" "%~1/gpujpeg.lib" -o "%TASK_ENCODE_CHECK%/gpujpeg-candidate.exe"
