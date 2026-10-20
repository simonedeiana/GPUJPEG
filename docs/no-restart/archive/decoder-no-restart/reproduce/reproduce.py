"""Portable transfer-inclusive benchmark; requires the supplied decode_bench binaries."""
from pathlib import Path
import argparse, hashlib, json, random, statistics, subprocess

p = argparse.ArgumentParser()
p.add_argument('--locked', type=Path, required=True)
p.add_argument('--candidate', type=Path, required=True)
p.add_argument('--fixtures', type=Path, required=True)
p.add_argument('--out', type=Path, required=True)
p.add_argument('--rounds', type=int, default=5)
p.add_argument('--iterations', type=int, default=110)
p.add_argument('--warmup', type=int, default=10)
args = p.parse_args()
args.out.mkdir(parents=True, exist_ok=False)
manifest = json.loads((args.fixtures / 'fixture-manifest.json').read_text())
def sha(path):
    with path.open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()
assert all(sha(args.fixtures / r['filename']) == r['sha256'] for r in manifest)
names = ['hd-rgb-q90-444-interleaved-reference.jpg', 'hd-rgb-q90-422-interleaved-reference.jpg',
         'hd-rgb-q90-420-interleaved-reference.jpg', 'hd-gray-q90-gray-interleaved-reference.jpg',
         '4k-rgb-q90-444-interleaved-reference.jpg', '4k-rgb-q90-422-interleaved-reference.jpg',
         '4k-rgb-q90-420-interleaved-reference.jpg', '4k-gray-q90-gray-interleaved-reference.jpg',
         'hd-rgb-optimized-2.jpg', '4k-rgb-optimized-2.jpg', 'flat-512-0.jpg', 'flat-512-1.jpg',
         'flat-1920-0.jpg', 'flat-1920-1.jpg', 'tiny-black-gray-q75-gray-interleaved-reference.jpg']
rng = random.Random(20261021)
records = []
for round_index in range(args.rounds):
    order = list(names); rng.shuffle(order)
    for name in order:
        codecs = ['locked', 'candidate']; rng.shuffle(codecs)
        for codec in codecs:
            output = args.out / 'pixels.raw'
            run = subprocess.run([str(getattr(args, codec).resolve()), str((args.fixtures/name).resolve()),
                str(args.iterations), str(args.warmup), str(output.resolve())], capture_output=True, check=True)
            prefix = f'{len(records):03d}-{codec}'
            (args.out / (prefix+'.stdout.log')).write_bytes(run.stdout)
            (args.out / (prefix+'.stderr.log')).write_bytes(run.stderr)
            records.append(dict(round=round_index, case=name, codec=codec,
                                timing=json.loads(run.stdout), output_sha256=sha(output)))
            assert output.resolve().parent == args.out.resolve()
            output.unlink()
            (args.out/'results.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
summary = []
for name in names:
    runs = [r for r in records if r['case']==name]
    assert len({r['output_sha256'] for r in runs}) == 1, name
    row = dict(case=name)
    for codec in ['locked', 'candidate']:
        row[codec] = statistics.median(r['timing']['median_ms'] for r in runs if r['codec']==codec)
    row['speedup'] = row['locked']/row['candidate']
    summary.append(row)
(args.out/'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(json.dumps(summary, indent=2))
