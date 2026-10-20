> Archived investigation document. See the [current branch guide](../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# Reproducing the nvJPEG observations

Use your own installed NVIDIA CUDA13.4 toolkit. The investigated DLL SHA256 is
`957bb73818e62883ac4bfc0aa15204bba5e341a8f7964a11b0289742df4e0da2`.
Another DLL version can have different kernels, layouts and behavior. None of
the vendor DLL, full extracted PTX/SASS or unfiltered strings record is included
in the user deliverables. The focused `rea-evidence-summary.json` retains
original Evidence IDs, full-record hashes, commands and selected facts.

## REA inventory

REA6.7.0 and an existing Rizin executable generated the records. Use your
installed `rea` CLI, or the equivalent version-pinned package entry point. Set
the provider variable only for the current PowerShell process; no REA client
configuration change is required:

```powershell
$env:REA_RIZIN_COMMAND = 'C:\your\rizin\bin\rizin.exe'
$dll = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.4\bin\x64\nvjpeg64_13.dll'
Get-FileHash -Algorithm SHA256 -LiteralPath $dll
rea execute-rizin-command $dll 'iEj' --format json > private-exports.json
rea execute-rizin-command $dll 'iIj;izj' --format json > private-info-strings.json
rea execute-rizin-command $dll 'pdj 80 @ 0x18001eb60;pdj 60 @ 0x180020370;pdj 50 @ 0x180020a20' --format json > private-encode-api.json
```

The unfiltered string result can contain complete embedded vendor PTX. Keep
that record private. JSON formatting and line-ending differences can change a
saved-record hash even when the target DLL and underlying observations match.
The focused summary records the exact invocation used here. Kernel names
alone prove inventory, not executed paths; use the runtime traces for the latter.

## Local instruction inspection

CUDA's supplied `cuobjdump` was used for independent instruction inspection:

```powershell
$cuda = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.4'
& "$cuda\bin\cuobjdump.exe" --version
& "$cuda\bin\cuobjdump.exe" --dump-ptx $dll > private-nvjpeg.ptx
& "$cuda\bin\cuobjdump.exe" --gpu-architecture sm_86 --dump-sass --function '_ZN6nvjpeg8encoding19BlockAssembleKernelINS_20ScanOrderInterleavedEEEvT_PjPKyPKjyPKiyyi,_ZN6nvjpeg8encoding8DCKernelILNS0_8WARP_COME0EEEvNS_9ScanOrderEPKsyPjPiPK5uint2yyii,_ZN6nvjpeg8encoding18ByteStuffingKernelEPhPKyPKhi' $dll > private-encoding-sm86.sass
```

Private line anchors in `analysis.md` refer to the exact hashed files generated
in this investigation. Different output encoding, cuobjdump versions or DLL
bytes can move line numbers; match the function name and instruction-relative
offsets as well. Do not package these full extracted vendor-code files with
the independent GPUJPEG implementation.

## Public API timing harness

Build `nvjpeg_encode.cpp` using `build.cmd`, with MSVC and CUDA13.4 installed.
Adjust the MSVC initialization path if needed. The executable dynamically links
your installed nvJPEG; prepend its directories to PATH:

```powershell
$env:PATH = "$cuda\bin\x64;$cuda\bin;$env:PATH"
.\nvjpeg_encode.exe --input hd-rgb.raw --width 1920 --height 1080 --channels 3 --sampling 444 --quality 90 --optimized 0 --iterations 110 --warmup 10 --output hd444.jpg
```

Raw RGB input is exactly width×height×3 bytes, interleaved RGBRGB..., with no
header. Gray is width×height bytes with `--channels 1 --sampling gray`. Other
sampling choices are420 and422 for RGB. Default host input and compressed output
buffers are pageable. File I/O, allocation and setup are outside the timer;
every sample includes raw H2D, encoding, compressed output retrieval and stream
synchronization. JSON output retains all samples and a restart-free marker
audit. Do not pass preconverted planar buffers as packed RGB input.

For a focused runtime trace, prefix the same command with:

```powershell
nsys profile --trace=cuda --sample=none --force-overwrite=true -o hd444 .\nvjpeg_encode.exe --input hd-rgb.raw --width 1920 --height 1080 --channels 3 --sampling 444 --quality 90 --optimized 0 --iterations 30 --warmup 10 --output hd444.jpg
nsys export --type=sqlite --force-overwrite=true --output hd444.sqlite hd444.nsys-rep
```

The saved JPEG must contain no restart markers and absent or zeroDRI. Verify
complete host output, not a discarded or device-only result. There are two small
metadata downloads plus an entropy-sized output download, so total JPEG file
size is not the required size of one CUDA D2H operation. Profiled kernel and DMA
durations remain diagnostics; unprofiled host samples are the primary latency.
