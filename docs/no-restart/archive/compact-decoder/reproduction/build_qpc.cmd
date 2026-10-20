@echo off
if not defined CAPTURE_VCVARS set "CAPTURE_VCVARS=C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat"
call "%CAPTURE_VCVARS%"
if errorlevel 1 exit /b %errorlevel%
cl /nologo /EHsc /O2 /MD /LD "%~dp0qpc_capture.cpp" /Fe"%~dp0qpc_capture.dll" /Fo"%~dp0qpc_capture.obj" /link /IMPLIB:"%~dp0qpc_capture.lib"
if errorlevel 1 exit /b %errorlevel%
cl /nologo /EHsc /O2 /MD /DQPC_CAPTURE_SELFTEST "%~dp0qpc_capture.cpp" /Fe"%~dp0qpc_overhead.exe" /Fo"%~dp0qpc_overhead.obj"
