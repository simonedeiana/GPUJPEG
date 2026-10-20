> Archived investigation document. See the [current branch guide](../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# JPEG investigation outputs

## Current work: restart-free GPU decoding

[Decoder results](decoder-no-restart/report.md) · Source (`artifact: decoder-no-restart/GPUJPEG-decoder-source.zip`) · Patch (`artifact: decoder-no-restart/decoder-no-restart.patch`) · [Build and reproduction](decoder-no-restart/README.md)

An independently implemented speculative GPU decoder accelerates the tested HD/4K natural-image cases by 3.06–6.39x including transfers. All 102 restart-free fixtures match original GPUJPEG's coefficients and pixels exactly. Small-image regressions and synchronization limits are documented; the source option defaults OFF. Decoder changes are on `decoder-r0-development`, commit `74911dfee71e4d5c278427ea04e84a63355bf082`.

## Locked implementation: restart-free GPU encoding

[Results and explanation](nvjpeg-no-restart/report.md) · Source (`artifact: nvjpeg-no-restart/GPUJPEG-no-restart-source.zip`) · Patch (`artifact: nvjpeg-no-restart/gpujpeg-no-restart.patch`) · Windows SM86 encoder (`artifact: nvjpeg-no-restart/gpujpegtool.exe`)

REA-guided nvJPEG analysis led to a new GPUJPEG encoding path for restart interval zero. On the tested HD/4K configurations it is 7.7–13.0× faster than original GPUJPEG, including transfers, while preserving its exact JPEG output. nvJPEG retains lower latency. The report includes tiny-image regressions, quality comparisons, memory costs and precise validation limits.

The encoder is preserved at tag `encoder-r0-locked-20261020`; [the lock record](encoder-locked/LOCK.json) hashes the unchanged encoder artifacts. Use the complete, clone-tested encoder bundle (`artifact: decoder-no-restart/encoder-locked-complete.bundle`) for recovery; the original shallow bundle is retained unchanged for provenance. [Build and reproduction instructions](nvjpeg-no-restart/README.md), [native analysis](nvjpeg-no-restart/analysis.md), [implementation](nvjpeg-no-restart/implementation.md), [timing/quality data](nvjpeg-no-restart/benchmark.csv) and [integrity manifest](nvjpeg-no-restart/manifest.json) accompany it. Current development adds the experimental decoder without changing the encoder implementation files.

## Historical investigations, preserved for reference

- [Original GPUJPEG versus Fastvideo](original-comparison/report.md): transfer-inclusive decoder comparison of the two original implementations.
- [Compact tables and parallel unstuffing](compact-decoder/report.md): experimental decoder ports, including their regressions and limited improvements.
- [Separate Huffman/RLE/DC stages](staged-decoder/report.md): earlier staged decoder investigation.
- [Initial Fastvideo investigation](fast-jpeg-investigation.md) and [decoder analysis](decoder-analysis.md): binary/runtime findings and earlier source packages.

These packages preserve previous source and evidence. They are not part of the current encoder patch.

## Cleanup

[Cleanup record](cleanup/README.md) documents removal of 8.17 GiB of regenerable data and consolidation of remaining old scratch work into a verified recoverable archive. Original inputs/software, REA tools, the unmodified baseline and the new investigation were preserved.
