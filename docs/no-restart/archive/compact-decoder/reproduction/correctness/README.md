> Archived investigation document. See the [current branch guide](../../../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# Independent compact-table / parallel-unstuff correctness checks

The harness `decoder_dump.cu` decodes through the public GPUJPEG API and then
reads the private quantized `int16_t` coefficient buffer. It serializes all 64
coefficients per block in the coder's block-list order, excluding allocator
tails, and writes the exact public output pixel buffer plus image metadata.
Build every harness against a static library from the same GPUJPEG revision
and OpenGL configuration. This private-layout test is not a public API.

## Executed candidate checks

| Candidate | Fresh | Same instance reuse | Concurrent independent instances | Total |
|---|---:|---:|---:|---:|
| Compact tables + parallel unstuff | 38 | 34 | 68 | 140 |
| Both features + constant-table build option | 38 | 34 | 68 | 140 |
| Refactored staged control, neither feature | 38 | — | — | 38 |
| Compact tables only | 38 | — | — | 38 |
| Parallel unstuff only | 38 | — | — | 38 |

All **394 comparisons** passed exact SHA-256 equality of every coefficient byte
and every public output pixel byte, and equal image/buffer metadata. The
original unmodified decoder supplies fresh-instance reference dumps. Each
candidate instance decodes two passes; concurrent mode uses two actual host
threads, independent nonblocking CUDA streams and decoder instances traversing
opposite input orders. Decoder source, helper header, libraries and linked
harnesses remained unchanged during the recorded runs.

The 38 inputs comprise the earlier 28-fixture corpus plus ten independent
known-coefficient JPEGs. Of these, 34 have at least 32 restart segments and
GPU-supported Huffman table selectors. The other four exercise the existing CPU
entropy fallback, including a grayscale image with 31 segments and JPEGs without
restart markers. The image sizes alone are not used to infer GPU entropy use.
The actual JPEG-header audit records nine distinct DHT identities.

The compact path stores canonical tables per decoder instance and compiles out
the shared global quick-Huffman-table refresh. With that path enabled, the
constant-table option only selects the identical natural-order constant for
the RLE scatter. Consequently, concurrent different-DHT decoding was tested
for the combined constant-table build too. This does not extend the original
expanded-table constant-memory mode's concurrency contract.

## Rare-code and byte-boundary coverage

`make_stress.py` writes baseline 8-bit grayscale JPEGs directly from known
quantized coefficients, using independently constructed canonical Huffman
tables and a normal JPEG bitstream writer. The generator does not use either
GPUJPEG decoder. Optional Pillow/libjpeg validation passed for all ten inputs.
The original decoder's coefficient dumps also equal the known coefficient
files exactly, and candidate outputs must match both sources of evidence.

The stress cases use actual 16-bit Huffman codewords, including an AC code
`0xfffe`, and a second table with very large unused canonical ranges. There are
33,920 actually emitted 16-bit symbols. Dense blocks use all 64 token slots;
sparse cases exercise EOB and ZRL; DC differences reach category 11. Restart
intervals 1, 2, 3, 7 and 17 each produce 41 segments, with a one-block final
segment. Segment byte lengths range from 1 to 1,957, including first, middle
and final one-byte segments. Forty-one segments also exercise the last partial
eight-warp thread block.

Stuffed `FF 00` pairs cross the following boundaries **relative to each entropy
segment's start**, rather than absolute file offsets:

| Boundary | Crossing pairs |
|---|---:|
| 16 bytes | 4,168 |
| 32 bytes | 2,024 |
| 64 bytes | 832 |
| 128 bytes | 480 |
| 256 bytes | 240 |
| 512 bytes | 120 |

These include lane-zero reads of the prior chunk's final `FF` in the warp's
32-byte unstuffing loop, full chunks and partial tails.

## Sanitizers and independent review

Six strict Compute Sanitizer runs returned exit code 0 and explicit zero-error
or zero-hazard summaries. The filter `kns=gpujpeg_huffman_` targets the unstuff,
token, RLE and DC kernels in the combined build:

* `memcheck`, `racecheck`, `synccheck` on `chain16-r17.jpg`, two decode iterations.
* `memcheck` on `holes16-r17.jpg`, two iterations.
* `memcheck` on two deliberately invalid entropy diagnostics: empty segments and
  an unused canonical prefix `0xffff`. These are bounded-access checks, excluded
  from the 394 valid-image comparison count. The decoder accepted both with no
  targeted memory errors; this is not a claim of general malformed-input
  rejection or hardening.

An earlier launch-dump invocation is retained separately as routing diagnostics.
It is not counted as a clean sanitizer result. Headline sanitizer evidence is
the `*-strict.log` files and `sanitizers/results-strict.json`.

Static review found and prompted a fix before validation: the four-step
canonical search only produces indices 0 through 15, so `index >= 16` could not
reject unused prefixes above the last canonical upper bound. The final helper
explicitly checks `prefix >= limit[15]` before searching.

The unstuff slot layout rounds each source start up to 16-byte alignment and
adds 32 bytes per segment. For ordered, disjoint input segments this leaves at
least 17 bytes between a worst-case unstuffed tail and the following slot,
enough for the 16-byte zero guard. The input predecessor read is guarded by a
valid in-segment offset greater than zero; an empty segment performs no input
read. The word reader loads at most three guard bytes beyond a partial word,
and its reservoir holds at most 47 live bits. Size arithmetic and capacity
growth were also reviewed; the malformed-prefix fix above was the identified
new defect.

## Reproduce without archived images or coefficient dumps

The source package needs Python 3.9+, Pillow with JPEG restart-marker support,
a CUDA GPU/toolkit and a C++17 compiler. No raw image or coefficient dump is
required from this analysis. Supply the two 8-bit color PPM images and grayscale
PGM from your local software package, or equivalent local images.

Build an unmodified reference GPUJPEG at revision
`78e5eee3f75e0a2a2e6c1409000ac05d679b4af8` and the candidate variants as static
Release libraries, OpenGL OFF. Use your GPU's CUDA architecture. Compile the
same `decoder_dump.cu` against each library. A Windows developer-shell example:

```bat
nvcc -O2 -std=c++17 -Xcompiler /MD -Xcompiler /Zc:__cplusplus -DGPUJPEG_STATIC -I SOURCE -I BUILD -c decoder_dump.cu -o decoder_dump.obj
nvcc -Xcompiler /MD decoder_dump.obj BUILD/gpujpeg.lib -o dump.exe
```

The CRT option must match in both compilation and linking. On Linux, use the
corresponding static library and host pthread flag, for example
`-Xcompiler=-pthread`. The local `build-harnesses.cmd` and `link-const.cmd` record
the executed Windows configuration; adapt their toolchain paths.

Generate ten natural comparison JPEGs using only the original encoder, then
the eighteen extra baseline fixtures and ten independent rare-code fixtures:

```sh
python generate_corpus.py --encoder REF/gpujpegtool --hd-rgb wild.8.ppm --large-rgb wild.4k.8.ppm --gray wild.8.pgm --output inputs/corpus
python generate_base.py --fixtures-only --reference-tool REF/gpujpegtool --corpus-manifest inputs/corpus/manifest.json --output inputs/base
python make_stress.py --output inputs/stress
```

The generator scripts are bundled here. `generate_base.py` is adapted from the
previous fixture runner and supports relative corpus manifest paths. Pillow
version changes may change generated JPEG bytes; all candidates in a run must
consume the same recorded input bytes.

Create reference dumps locally and validate a candidate:

```sh
python validate_compact.py --old-reference-root inputs/base --regenerate-original-reference --stress-manifest inputs/stress/manifest.json --reference-harness dump-reference --candidate combined=dump-combined --output checks/combined --source SOURCE/src/gpujpeg_huffman_gpu_decoder.cu --library input_helper=SOURCE/src/gpujpeg_huffman_staged_input.cuh
```

The `--regenerate-original-reference` option makes this independent of the old
dump cache. Input manifests support relative or absolute paths and require
SHA-256 identities. Repeat with the constant-table build, and use `--fresh-only`
for ablations. To validate multiple variants together, repeat `--candidate`.

For example, targeted memory checking is:

```sh
compute-sanitizer --tool memcheck --kernel-name kns=gpujpeg_huffman_ --error-exitcode 99 CANDIDATE/gpujpegtool -d -n 2 inputs/stress/chain16-r17.jpg check.pgm
```

Substitute `racecheck` or `synccheck` for the other checks. `make_negative.py`
generates the two separately labeled invalid diagnostics beside its local
`stress/` directory; they are never inserted into the valid-image manifest.

## Evidence and limits

Results and identities are in `combined/`, `const/` and `ablations/`:
`results-*.json`, `summary.json`, `provenance-before.json` and
`provenance-after.json`. The actual input audit is
`combined/jpeg-dht-audit.json`; detailed relative FF00 offsets and known
coefficient identities are in `stress/manifest.json`. Full valid-run logs are
`validate-combined.log`, `validate-const.log` and `validate-ablations.log`.

These experiments preserve GPUJPEG's baseline 8-bit feature scope. Fewer than
32 segments and streams without restart markers continue through its CPU
entropy fallback. They do not add Fastvideo's CPU restart-insertion/re-encoding
path or claim GPU entropy decoding of ordinary no-restart JPEGs.

Same-instance resize tests use GPU-eligible images to avoid an independently
isolated pre-existing baseline host coefficient-buffer resize bug in mixed
GPU/CPU sequences. All inputs, including CPU fallbacks, are verified with fresh
instances. The earlier staged-decoder report documents the exact three-image
reproducer; it is not changed by this decoder experiment.

## Packaged evidence locations

The original run-folder names above are retained as provenance. In this
package the recorded JSON/log evidence is under `../../evidence/`: prefixes
`combined-`, `const-` and `correctness-ablations-` identify their original
folders. `combined-jpeg-dht-audit.json`, `stress-manifest.json` and
`sanitizers/*-strict.log` retain input coverage and sanitizer results.
The self-contained generators were replayed after all benchmarks; 28 base
plus ten stress input identities matched, and 38 new fresh checks passed
without archived reference dumps (`portable-replay*.json`).
