> Archived investigation document. See the [current branch guide](../../../../README.md) for build options and final results. Local paths are normalized; generated binary/archive artifact references are retained as text.

# Independent staged JPEG decoder verification

`decoder_dump.cu` is a private-internal test harness, not a public library API.
Build it twice against static GPUJPEG libraries from the **same source revision**:
the unmodified baseline and the staged candidate. It uses the public decoder API
and then copies the quantized signed 16-bit DCT coefficients back from the CUDA
buffer. Coefficients are serialized as 64 values per block in the coder's block
list order. It also writes the exact public decoded pixel buffer and metadata.
Allocator tails outside the block list are excluded.

Example compilation on Windows, in a Visual Studio x64 developer shell:

```bat
nvcc -O2 -std=c++17 -Xcompiler /MD -Xcompiler /Zc:__cplusplus -DGPUJPEG_STATIC -I SOURCE -I BUILD decoder_dump.cu BUILD/gpujpeg.lib -o dump.exe
```

Use the relevant source/include and build/library paths for each variant. On
Linux, link the corresponding static library and CUDA runtime and pass the host
compiler's pthread option (for example `-Xcompiler=-pthread`). Both builds must
use OpenGL OFF so the private struct layout matches the harness. The attached
`build-dumps.cmd` and `build-const-dump.cmd` reproduce the local Windows builds;
their paths identify the local toolchain and can be adapted.

The portable Python runner requires Pillow with JPEG restart-marker support:

```sh
python validate_decoder.py --reference dump-baseline --candidate dump-staged \
  --reference-tool baseline/gpujpegtool --output validation \
  [--corpus-manifest corpus/manifest.json] [--pillow-path extra/site-packages]
```

The optional corpus manifest is an array containing `name` and `path`
for each natural JPEG. Relative paths resolve beside the manifest. This
resolution was added to the packaged runners for portability; the executed
validation used absolute paths and its exact script hashes remain in provenance.
Without a corpus manifest, all synthetic fixtures are generated locally.
The runner independently compares SHA-256 of **all coefficient bytes and all
public pixel bytes**, and requires equal dimensions, layouts and buffer sizes.

The fixture set includes 4:4:4, 4:2:2 and 4:2:0; odd dimensions 641x479;
Pillow-optimized custom Huffman tables; dense Q100 and sparse EOB blocks;
planar component scans; grayscale with exactly 31, 32 and 33 restart segments;
and restart intervals spanning multiple 32-block DC predictor tiles. Fresh
instances cover every input, including no-restart CPU fallback. Then images
with at least 32 segments are decoded twice in order by one reused instance,
and by two actual concurrent host threads with independent nonblocking streams
and decoder instances traversing opposite input orders.

`--reuse-reference` avoids repeating the baseline runs after relinking a new
candidate. It hashes existing inputs before regeneration and refuses changed
input identities. Use `--candidate-label LABEL` for separate candidate dump
folders and result JSON, preserving prior results. For
`HUFFMAN_GPU_CONST_TABLES=ON`, use `--skip-dual`: the existing global constant
tables do not establish a concurrent-decoder contract for different Huffman
tables. Fresh and sequential instance reuse are the tested contract here.

`audit_jpeg_routes.py MANIFEST` inspects the actual JPEG SOF/DHT/DRI/SOS bytes.
It records DHT digests and checks the GPU decoder's table-index routing
conditions. The dump metadata records the actual segment count. Together they
distinguish GPU entropy coverage from the `<32`-segment CPU fallback; a large
pixel image alone does not prove GPU entropy decoding.

## Executed results on RTX 3050, CUDA 13.4

These counts refer to the final shared-transpose RLE variant (`tile[64][9]`),
raw amplitude tokens and separate DC predictor scan. Earlier thread-per-block
RLE results are retained separately as `run/prior-thread-rle-results*.json`.

* Final staged decoder, default table-memory configuration: **100/100** exact
  coefficient **and** public-pixel comparisons over 28 inputs. Fresh 28,
  sequential reuse 24, concurrent dual instances 48. Of the fresh cases,
  24 have at least 32 restart segments; the remaining four exercise CPU
  fallback. Each reused instance decodes two full passes through its sequence.
* Final staged decoder with constant Huffman tables: **52/52** exact comparisons
  over the same 28 inputs: fresh 28 and sequential reuse 24. Concurrent
  different-table behavior with this option was not tested or claimed.
* Actual JPEG header audit: all 28 fixtures use GPU-supported table selector
  combinations; inspect `run/jpeg-dht-audit.json` for the DHT identities.

Machine-readable results: `run/results.json`, `run/results-const.json`.
Final run logs: `validate-final-transpose.log`, `validate-final-transpose-const.log`.
Exact source, library and linked harness identities are recorded in
`final-provenance-before.json` and `final-provenance-after.json`.

## Existing upstream resize limitation isolated during verification

The unmodified baseline library access-violates on this one-instance sequence:

1. Decode `4k420-r10.jpg` using GPU entropy.
2. Decode `hd444-r0.jpg` using CPU entropy, allocating a smaller host coefficient
   buffer while retaining the larger device allocation.
3. Decode `4k420-r0.jpg` using CPU entropy into a larger host coefficient buffer.

Reproducer (paths from the natural corpus):

```sh
mkdir reproduce
dump-baseline reproduce single corpus/4k420-r10.jpg corpus/hd444-r0.jpg corpus/4k420-r0.jpg
```

Observed Windows exit status: `3221225477` (access violation), before the harness
reads any private coefficient data. The isolated two CPU-image sequence
`hd444-r0.jpg -> 4k420-r0.jpg` succeeds because device allocation growth also
invalidates the host coefficient buffer. Source inspection finds that
`gpujpeg_coder_allocate_cpu_huffman_buf()` sizes host coefficients on first
allocation, while `gpujpeg_coder_init_image()` frees that host buffer only when
the device coefficient allocation grows. The decoder calls the host allocator
only when its pointer is NULL. This predates the staged patch; it is not fixed
here. Fresh-instance verification covers all inputs, and the mixed-resize reuse
tests are restricted to GPU entropy paths to avoid hiding this upstream failure.
The exact reproduction log is `run/upstream-cpu-grow-minimal.log`.
