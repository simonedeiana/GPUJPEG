@echo off
call "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat"
set "TASK_CHECK=work/compact-decode/correctness"
nvcc -O2 -std=c++17 -Xcompiler /MD -Xcompiler /Zc:__cplusplus -DGPUJPEG_STATIC -Iwork/GPUJPEG -Iwork/build-compact-unstuff -c "%TASK_CHECK%/decoder_dump.cu" -o "%TASK_CHECK%/decoder_dump.obj"
if errorlevel 1 exit /b %errorlevel%
nvcc -Xcompiler /MD "%TASK_CHECK%/decoder_dump.obj" work/build-compact-unstuff/gpujpeg.lib -o "%TASK_CHECK%/dump-combined.exe"
if errorlevel 1 exit /b %errorlevel%
nvcc -Xcompiler /MD "%TASK_CHECK%/decoder_dump.obj" work/build-compact-control/gpujpeg.lib -o "%TASK_CHECK%/dump-control.exe"
if errorlevel 1 exit /b %errorlevel%
nvcc -Xcompiler /MD "%TASK_CHECK%/decoder_dump.obj" work/build-compact-only/gpujpeg.lib -o "%TASK_CHECK%/dump-compact.exe"
if errorlevel 1 exit /b %errorlevel%
nvcc -Xcompiler /MD "%TASK_CHECK%/decoder_dump.obj" work/build-unstuff-only/gpujpeg.lib -o "%TASK_CHECK%/dump-unstuff.exe"
