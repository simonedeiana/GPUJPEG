@echo off
call "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat"
nvcc -Xcompiler /MD work/compact-decode/correctness/decoder_dump.obj work/build-compact-unstuff-const/gpujpeg.lib -o work/compact-decode/correctness/dump-combined-const.exe
