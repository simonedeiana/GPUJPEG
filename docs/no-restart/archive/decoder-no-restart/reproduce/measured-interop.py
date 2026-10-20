from pathlib import Path
import hashlib, json, subprocess
ROOT = Path(__file__).resolve().parents[2]
W = ROOT / 'work/decoder-r0'
inputs = [ROOT / 'work/encode-r0-validation/first-validation' / name for name in [
    'hd-rgb-q90-420-interleaved-reference.jpg', '4k-rgb-q90-420-interleaved-reference.jpg',
    'noise-gray-q90-gray-interleaved-reference.jpg', 'noise-rgb-q100-420-planar-reference.jpg']]
inputs += [W / 'fixtures/hd-rgb-optimized-2.jpg', ROOT / 'work/new-decode-benchmark/corpus/hd444-r10.jpg']
records = []
for codec, binary in [('locked', ROOT / 'work/encode-r0-validation/dump-baseline.exe'),
                       ('candidate', W / 'dump-candidate.exe')]:
    out = W / 'interop' / codec
    out.mkdir(parents=True, exist_ok=True)
    run = subprocess.run([str(binary), str(out), 'dual', *map(str, inputs)], capture_output=True, timeout=180)
    (out / 'run.log').write_bytes(run.stdout + run.stderr)
    if run.returncode: raise RuntimeError(run.stderr[-2000:])
    for worker in range(2):
        for i, source in enumerate(inputs):
            hashes = {}
            for ext in ['coefficients', 'pixels']:
                p = out / f'w{worker}-{i:03d}.{ext}'
                hashes[ext] = hashlib.file_digest(p.open('rb'), 'sha256').hexdigest()
                assert p.resolve().is_relative_to(W.resolve()); p.unlink()
            records.append(dict(codec=codec, worker=worker, input=str(source), hashes=hashes))
for i in range(len(inputs)):
    group = [r for r in records if r['input'] == str(inputs[i])]
    assert len({json.dumps(r['hashes'], sort_keys=True) for r in group}) == 1, inputs[i]
(W / 'interop-summary.json').write_text(json.dumps(dict(cases_per_worker=len(inputs),
    workers_per_codec=2, all_equal=True, records=records), indent=2), encoding='utf-8')
print('12 concurrent/mixed-RI outputs equal across both workers and both codecs')
