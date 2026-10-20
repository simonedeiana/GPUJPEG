# Reproduction from the combined branch

The [branch guide](README.md) gives build options. Use separate build directories and static libraries for original upstream, encoder-only, and combined configurations. The encoder-only source is recoverable with `git checkout encoder-r0-locked-20261020`; unmodified upstream is `78e5eee3f75e0a2a2e6c1409000ac05d679b4af8`. Do not reset an edited checkout; clone or create a separate worktree instead.

## Encoder

[Original harness instructions](archive/nvjpeg-no-restart/reproduction/README.md), [methodology](archive/nvjpeg-no-restart/methodology.md), and [native-analysis reproduction](archive/nvjpeg-no-restart/nvjpeg-reproduce.md) specify the actual measurement boundary, seed, warmups, state checks and sanitizer scope. Source files are in `archive/nvjpeg-no-restart/reproduction/`. Original paths are normalized to `<WORKSPACE>`/`<USER_PROFILE>` and must be replaced with local paths. Prepare your own raw inputs; hashes identify the measured images without redistributing them.

## Decoder

The original [decoder instructions](archive/decoder-no-restart/README.md) describe the static harness build. Use `archive/decoder-no-restart/reproduce/decode_bench.cu` for the public-API timing boundary, and `decoder_dump.cu` in the same directory for coefficient/pixel parity. Build against either unmodified upstream or the combined library. The timing CLI is `decode_bench JPEG ITERATIONS WARMUP OUTPUT_RAW`; the dump CLI is `decoder_dump OUTPUT_DIRECTORY single|dual JPEG [JPEG ...]` (create the output directory first).

The portable `reproduce.py` driver accepts `--locked`, `--candidate`, `--fixtures`, `--out`; its fixture manifest and exact names are in `archive/decoder-no-restart/evidence/fixture-manifest.json`. The measured fixture JPEGs and executables were part of the local delivery and are not redistributed in this public repository. Supply the corresponding locally owned fixtures, or benchmark your own images directly with the timing harness and label that a new matrix. A new matrix should use identical JPEG bytes for both implementations and compare coefficient/pixel hashes. Keep the GPU exclusive while timing. Original scheduler/validation source under `measured-*` describes the 102-case run and needs local path substitution.

The recorded decoder benchmark uses five seeded randomized rounds, 110 calls per process, first 10 discarded, and medians of round medians. Each public call includes parsing, all transfers, GPU processing, complete host output and final stream synchronization. Disk IO and decoder creation are outside the timer. Profiling and sanitizer runs are separate. Validation data is in `archive/decoder-no-restart/evidence/`, and per-run logs/metadata in `archive/decoder-no-restart/raw-evidence/`.

No performance tests were rerun merely to publish this branch: source files match the validated encoder/decoder commits. Documentation edits do not change measured code. The recorded artifact hashes identify the original machine-specific binaries rather than binaries rebuilt from this repository today.
