from pathlib import Path
import hashlib, json, sqlite3, sys

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'work/decoder-r0'
sys.path.insert(0, str(ROOT / 'work/encode-r0-validation'))
from jpeg_checks import JPEG

def sha(p):
    with Path(p).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()
def save(name, data): (WORK / name).write_text(json.dumps(data, indent=2), encoding='utf-8')

audits = []
for filename in json.loads((WORK / 'corpus.json').read_text()):
    p = Path(filename)
    a = JPEG(p.read_bytes()).audit(True)
    assert a['true_ri0'] and a['rst_count'] == 0
    audits.append(dict(filename=p.name, sha256=sha(p), bytes=p.stat().st_size,
                       width=a['width'], height=a['height'], entropy_bytes=a['entropy_bytes'],
                       scans=a['scan_count'], true_ri0=True))
assert len(audits) == 102 and len({a['filename'] for a in audits}) == 102
save('fixture-manifest.json', audits)
records = json.loads((WORK / 'validation-results.json').read_text())
zero = []
for r in records:
    if Path(r['input']).name.startswith('known-zero-'):
        c = r['candidate']['coefficients']
        assert c['sha256'] == hashlib.sha256(bytes(c['bytes'])).hexdigest()
        zero.append(dict(filename=Path(r['input']).name, all_coefficients_zero=True))
save('known-zero-summary.json', zero)

profiles = []
for name in ['hd420', '4k420']:
    db = sqlite3.connect(WORK / 'profiles' / (name + '.sqlite'))
    kernels = [dict(name=n, calls=c, total_ms=t/1e6, mean_us=t/c/1e3)
        for n,c,t in db.execute('SELECT s.value, count(*), sum(k.end-k.start) FROM CUPTI_ACTIVITY_KIND_KERNEL k JOIN StringIds s ON k.demangledName=s.id GROUP BY s.value ORDER BY sum(k.end-k.start) DESC')]
    copies = [dict(kind=k, bytes=b, calls=c, total_ms=t/1e6)
        for k,b,c,t in db.execute('SELECT copyKind,bytes,count(*),sum(end-start) FROM CUPTI_ACTIVITY_KIND_MEMCPY GROUP BY copyKind,bytes ORDER BY copyKind,bytes')]
    fixture = next(a for a in audits if a['filename'] == ('hd' if name == 'hd420' else '4k') + '-rgb-q90-420-interleaved-reference.jpg')
    entropy = fixture['entropy_bytes']
    assert any(c['kind']==1 and c['bytes']==entropy and c['calls']==30 for c in copies)
    assert any(c['kind']==2 and c['bytes']==fixture['width']*fixture['height']*3 and c['calls']==30 for c in copies)
    assert any(c['kind']==2 and c['bytes']==4 and c['calls']==30 for c in copies)
    assert all(any(stage in k['name'] for k in kernels) for stage in ['dr0_unstuff', 'dr0_speculate', 'dr0_reconcile', 'dr0_write', 'dr0_restore_dc'])
    profiles.append(dict(case=name, frames=30, fixture=fixture, kernels=kernels, copies=copies))
    db.close()
save('profile-summary.json', profiles)
config = json.loads((WORK / 'benchmark/configuration.json').read_text())
assert all(sha(p)==s for p,s in config['identities'].items())
save('final-integrity.json', dict(benchmark_source_and_binary_hashes_unchanged=True,
                                ri0_audited=len(audits), known_zero_cases=len(zero)))
print('102 RI0 audits, known-zero coefficients, profile stage/copy assertions, final hashes passed')
