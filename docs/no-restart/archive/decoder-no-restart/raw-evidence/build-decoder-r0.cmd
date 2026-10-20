@echo off
call "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat"
set "PATH=C:\Program Files\Microsoft Visual Studio\18\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin;C:\Program Files\Microsoft Visual Studio\18\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja;%PATH%"
cmake -S work/GPUJPEG -B work/build-decoder-r0 -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=86 -DBUILD_OPENGL=OFF -DBUILD_SHARED_LIBS=OFF -DHUFFMAN_GPU_NO_RESTART=ON -DHUFFMAN_GPU_NO_RESTART_DECODER=ON
if errorlevel 1 exit /b %errorlevel%
cmake --build work/build-decoder-r0 -j 4

