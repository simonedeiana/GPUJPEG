@echo off
if not defined CAPTURE_VCVARS set "CAPTURE_VCVARS=C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat"
call "%CAPTURE_VCVARS%"
if errorlevel 1 exit /b %errorlevel%
if not defined CAPTURE_CUDA set "CAPTURE_CUDA=%CUDA_PATH%"
if not defined CAPTURE_CUDA set "CAPTURE_CUDA=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.4"
cl /nologo /EHsc /O2 /MD /LD /I"%CAPTURE_CUDA%\include" /I"%CAPTURE_CUDA%\extras\CUPTI\include" "%~dp0capture.cpp" /Fe"%~dp0capture.dll" /Fo"%~dp0capture.obj" /link /IMPLIB:"%~dp0capture.lib" /LIBPATH:"%CAPTURE_CUDA%\extras\CUPTI\lib\x64" cupti.lib
