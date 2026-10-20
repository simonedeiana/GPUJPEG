@echo off
setlocal
call "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat"
if errorlevel 1 exit /b %errorlevel%
if not defined CUDA_PATH set "CUDA_PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.4"
pushd "%~dp0"
cl /nologo /O2 /EHsc /std:c++17 /I"%CUDA_PATH%\include" nvjpeg_encode.cpp /Fe:nvjpeg_encode.exe /link /LIBPATH:"%CUDA_PATH%\lib\x64" nvjpeg.lib cudart.lib
set "buildresult=%errorlevel%"
popd
exit /b %buildresult%
