from pathlib import Path
import hashlib, json, random, statistics, subprocess

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'work/decoder-r0'
OUT = WORK / 'benchmark'
OUT.mkdir(exist_ok=True)
names = ['hd-rgb-q90-444-interleaved', 'hd-rgb-q90-422-interleaved', 'hd-rgb-q90-420-interleaved',
         'hd-gray-q90-gray-interleaved', '4k-rgb-q90-444-interleaved', '4k-rgb-q90-422-interleaved',
         '4k-rgb-q90-420-interleaved', '4k-gray-q90-gray-interleaved']
cases = {n: ROOT / 'work/encode-r0-validation/first-validation' / (n + '-reference.jpg') for n in names}
for name in ['hd-rgb-optimized-2', '4k-rgb-optimized-2']:
    cases[name] = WORK / 'fixtures' / (name + '.jpg')
for name in ['flat-512-0', 'flat-512-1', 'flat-1920-0', 'flat-1920-1']:
    cases[name] = WORK / 'fixtures' / (name + '.jpg')
cases['tiny-gray-1x1'] = ROOT / 'work/encode-r0-validation/first-validation/tiny-black-gray-q75-gray-interleaved-reference.jpg'
binaries = dict(locked=WORK / 'bench-locked.exe', candidate=WORK / 'bench-candidate.exe')
def sha(path): return hashlib.file_digest(Path(path).open('rb'), 'sha256').hexdigest()
identities = {str(p): sha(p) for p in [*binaries.values(), *cases.values(),
    ROOT / 'work/GPUJPEG/src/gpujpeg_huffman_no_restart_decoder.cuh',
    ROOT / 'work/GPUJPEG/src/gpujpeg_huffman_gpu_decoder.cu', ROOT / 'work/GPUJPEG/src/gpujpeg_decoder.c',
    ROOT / 'work/GPUJPEG/CMakeLists.txt', ROOT / 'work/build-decoder-r0/gpujpeg.lib']}
rng = random.Random(20261021); schedule = []
for r in range(5):
    order = list(cases); rng.shuffle(order)
    for name in order:
        codecs = list(binaries); rng.shuffle(codecs)
        schedule.extend(dict(round=r, case=name, codec=c) for c in codecs)
(OUT / 'configuration.json').write_text(json.dumps(dict(iterations=110, warmup=10, rounds=5,
    boundary='Host JPEG before parse through complete HOST pixels and stream synchronization; no disk IO or initialization',
    identities=identities, schedule=schedule), indent=2), encoding='utf-8')
results = []
for index, job in enumerate(schedule):
    raw = OUT / 'current.raw'; name = f'{index:03d}-{job["case"]}-{job["codec"]}'
    run = subprocess.run([str(binaries[job['codec']]), str(cases[job['case']]), '110', '10', str(raw)],
                         capture_output=True, timeout=240)
    (OUT / (name + '.stdout.log')).write_bytes(run.stdout)
    (OUT / (name + '.stderr.log')).write_bytes(run.stderr)
    if run.returncode: raise RuntimeError(f'{name}: {run.stderr[-1000:]}')
    timing = json.loads(run.stdout)
    results.append(dict(**job, timing=timing, raw_sha256=sha(raw)))
    assert raw.resolve().is_relative_to(WORK.resolve()); raw.unlink()
    (OUT / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(index + 1, '/', len(schedule), name, timing['median_ms'], flush=True)
summary = []
for name in cases:
    runs = [r for r in results if r['case'] == name]
    assert len({r['raw_sha256'] for r in runs}) == 1, name
    row = dict(case=name, output_sha256=runs[0]['raw_sha256'])
    for codec in binaries:
        medians = [r['timing']['median_ms'] for r in runs if r['codec'] == codec]
        row[codec] = dict(median_ms=statistics.median(medians), round_medians=medians,
            min_round_median_ms=min(medians), max_round_median_ms=max(medians))
    row['speedup'] = row['locked']['median_ms'] / row['candidate']['median_ms']
    summary.append(row)
assert identities == {p: sha(p) for p in identities}, 'Inputs/source/binaries changed'
(OUT / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print('DONE', len(results), 'processes,', len(results) * 100, 'retained samples', flush=True)
