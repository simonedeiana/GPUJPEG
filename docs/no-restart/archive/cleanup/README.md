> Archived investigation document. See the [current branch guide](../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# Cleanup of earlier investigations

Earlier user-facing reports, benchmark evidence, source ZIPs and patches remain in their existing output locations so prior links keep working. The working GPUJPEG tree was restored to upstream before the new restart-free encoding implementation; earlier experimental decoder and warp serializer changes remain recoverable from the archived source packages.

- `removed-generated-files.json` records the exact path, length and SHA-256 of 1,735 regenerable decoder coefficient, pixel and decoded-image dumps (8,776,105,065 bytes) removed from scratch storage. The input corpus, result summaries, raw timing logs and reproduction code were retained.
- `archived-prior-work.json` records the remaining preceding scratch work consolidated into `work/previous-investigation.zip`, with an archive SHA-256 and per-file SHA-256. Every archive member was read back and verified before its original scratch file was removed. Extraction relative to `work/` restores the previous layout.
- Original supplied software and images, the unmodified GPUJPEG baseline, REA/Rizin, active source, current validation and nvJPEG evidence are kept separately. No files in Downloads or the CUDA installation were altered.

Large generated dumps can be reproduced using the retained test scripts and original corpus. The output source archives preserve the exact preceding code, including experimental options that were left disabled after their measurements.
