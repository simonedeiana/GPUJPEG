from pathlib import Path
import hashlib, json, subprocess

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'work/decoder-r0'
inputs = json.loads((WORK / 'corpus.json').read_text())
records = []
identities = {}
for name in ['dump-candidate.exe', '../encode-r0-validation/dump-baseline.exe']:
    p = WORK / name
    identities[str(p)] = hashlib.file_digest(p.open('rb'), 'sha256').hexdigest()
for number in range(0, len(inputs), 8):
    batch = inputs[number:number + 8]
    outputs = {}
    for label, binary in [('reference', ROOT / 'work/encode-r0-validation/dump-baseline.exe'),
                          ('candidate', WORK / 'dump-candidate.exe')]:
        folder = WORK / 'validation' / f'{number:03d}-{label}'
        folder.mkdir(parents=True, exist_ok=True)
        command = [str(binary), str(folder), 'single', *batch]
        run = subprocess.run(command, capture_output=True, timeout=180)
        (folder / 'run.stdout.log').write_bytes(run.stdout)
        (folder / 'run.stderr.log').write_bytes(run.stderr)
        if run.returncode:
            raise RuntimeError(f'{label} batch {number} failed: {run.stderr[-2000:]}')
        entries = []
        for i, source in enumerate(batch):
            entry = dict(input=source, jpeg_sha256=hashlib.file_digest(Path(source).open('rb'), 'sha256').hexdigest())
            entry['metadata'] = json.loads((folder / f'w0-{i:03d}.json').read_text())
            for extension in ['pixels', 'coefficients']:
                path = folder / f'w0-{i:03d}.{extension}'
                entry[extension] = dict(bytes=path.stat().st_size,
                    sha256=hashlib.file_digest(path.open('rb'), 'sha256').hexdigest())
                # Exact generated-file deletion after recording the content hash.
                assert path.resolve().is_relative_to(WORK.resolve())
                path.unlink()
            entries.append(entry)
        outputs[label] = entries
    for i, source in enumerate(batch):
        a, b = outputs['reference'][i], outputs['candidate'][i]
        equal = all(a[k] == b[k] for k in ['pixels', 'coefficients'])
        records.append(dict(input=source, equal=equal, reference=a, candidate=b))
        (WORK / 'validation-results.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
        if not equal: raise AssertionError(source)
    print(f'{len(records)}/{len(inputs)} coefficient+pixel parity PASS', flush=True)
(WORK / 'validation-summary.json').write_text(json.dumps(dict(cases=len(records),
    all_coefficients_and_pixels_equal=True, identities=identities), indent=2), encoding='utf-8')
