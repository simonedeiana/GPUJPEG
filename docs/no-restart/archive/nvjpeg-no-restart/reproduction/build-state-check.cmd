@echo off
call "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat"
set "TASK_STATE=work/encode-r0-validation"
nvcc -O2 -std=c++17 -Xcompiler /MD -Xcompiler /Zc:__cplusplus -DGPUJPEG_STATIC -Iwork/GPUJPEG -Iwork/build-baseline -c "%TASK_STATE%/encoder_state_check.cu" -o "%TASK_STATE%/encoder_state_check.obj"
if errorlevel 1 exit /b %errorlevel%
nvcc -Xcompiler /MD "%TASK_STATE%/encoder_state_check.obj" work/build-baseline/gpujpeg.lib -o "%TASK_STATE%/state-baseline.exe"
if errorlevel 1 exit /b %errorlevel%
nvcc -Xcompiler /MD "%TASK_STATE%/encoder_state_check.obj" "%~1/gpujpeg.lib" -o "%TASK_STATE%/state-candidate.exe"
