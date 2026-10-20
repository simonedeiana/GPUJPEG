"""Validate compact/unstuff decoder variants against original coefficient/pixel dumps.

The original 28-fixture SHA-256 manifest and fresh reference dumps are reused
read-only. New rare-Huffman fixtures have independently known coefficients.
All outputs are written only to --output; no prior result is changed.
"""
from pathlib import Path
import argparse,datetime,hashlib,json,subprocess

def digest(path):
    h=hashlib.sha256()
    with open(path,'rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def manifest(path):
    path=Path(path).resolve()
    entries=json.loads(path.read_text())
    for entry in entries:
        for field in ['path','expected_coefficients']:
            if field in entry:
                target=Path(entry[field])
                entry[field]=str((target if target.is_absolute() else path.parent/target).resolve())
    return entries

def invoke(binary,folder,mode,cases):
    folder.mkdir(parents=True,exist_ok=True)
    command=[str(binary.resolve()),str(folder.resolve()),mode,*[case['path'] for case in cases]]
    result=subprocess.run(command,capture_output=True,timeout=300)
    (folder/'decode.log').write_bytes(result.stdout+result.stderr)
    if result.returncode:raise RuntimeError(f'{binary.name} {mode} exited {result.returncode}; {folder}/decode.log')

def compare(reference,candidate,case,mode,worker,index):
    before=json.loads(reference.with_suffix('.json').read_text())
    after=json.loads(candidate.with_suffix('.json').read_text())
    # Reference is a fresh-instance single-file decode, so run-local index and
    # worker differ in reuse/concurrency modes. Every image property must agree.
    before_properties={k:v for k,v in before.items() if k not in ['index','worker']}
    after_properties={k:v for k,v in after.items() if k not in ['index','worker']}
    if before_properties!=after_properties:raise AssertionError(f'Metadata differs: {case["name"]}')
    row={'mode':mode,'case':case['name'],'worker':worker,'index':index,**before_properties}
    for suffix in ['coefficients','pixels']:
        expected=digest(reference.with_suffix('.'+suffix))
        actual=digest(candidate.with_suffix('.'+suffix))
        if expected!=actual:raise AssertionError(f'{suffix} differs: {mode} w{worker} {case["name"]}')
        row[suffix+'_sha256']=actual
    if 'expected_sha256' in case:
        if row['coefficients_sha256']!=case['expected_sha256']:
            raise AssertionError(f'Independent known coefficients differ: {case["name"]}')
        row['independent_known_coefficients']='exact'
    return row

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--old-reference-root',type=Path,required=True)
    p.add_argument('--stress-manifest',type=Path,required=True)
    p.add_argument('--reference-harness',type=Path,required=True)
    p.add_argument('--candidate',action='append',required=True,help='LABEL=PATH_TO_HARNESS')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source',type=Path,help='Frozen decoder source to identify before/after')
    p.add_argument('--library',action='append',default=[],help='LABEL=LIBRARY_PATH to identify')
    p.add_argument('--skip-dual',action='store_true')
    p.add_argument('--fresh-only',action='store_true')
    p.add_argument('--regenerate-original-reference',action='store_true',
                   help='Generate original baseline dumps locally instead of requiring a prior dump cache')
    args=p.parse_args();root=args.output.resolve();root.mkdir(parents=True,exist_ok=True)
    old=args.old_reference_root.resolve()
    original=manifest(old/'manifest.json')
    stress=manifest(args.stress_manifest);cases=original+stress
    candidates={}
    for item in args.candidate:
        label,path=item.split('=',1)
        if not label.replace('-','').replace('_','').isalnum():raise ValueError('Unsafe candidate label')
        candidates[label]=Path(path).resolve()
    for case in cases:
        if digest(case['path'])!=case['sha256']:raise RuntimeError(f'Fixture identity changed: {case["name"]}')
        if 'expected_coefficients' in case and digest(case['expected_coefficients'])!=case['expected_sha256']:
            raise RuntimeError(f'Known coefficient identity changed: {case["name"]}')
    (root/'input-manifest.json').write_text(json.dumps(cases,indent=2))
    identity_paths={'reference_harness':args.reference_harness.resolve(),**candidates}
    if args.source:identity_paths['decoder_source']=args.source.resolve()
    for item in args.library:
        label,path=item.split('=',1);identity_paths[label]=Path(path).resolve()
    def provenance():return {'recorded_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'files':{key:{'path':str(path),'bytes':path.stat().st_size,'sha256':digest(path)} for key,path in identity_paths.items()}}
    before=provenance();(root/'provenance-before.json').write_text(json.dumps(before,indent=2))

    references=[]
    for index,case in enumerate(original):
        if args.regenerate_original_reference:
            folder=root/'original-reference'/f'{index:03d}'
            invoke(args.reference_harness,folder,'single',[case])
            references.append(folder/'w0-000')
        else:
            references.append(old/'fresh-reference'/f'{index:03d}'/'w0-000')
    for index,case in enumerate(stress):
        folder=root/'stress-reference'/f'{index:03d}'
        invoke(args.reference_harness,folder,'single',[case])
        reference=folder/'w0-000'
        if digest(reference.with_suffix('.coefficients'))!=case['expected_sha256']:
            raise AssertionError(f'Original decoder failed independent coefficients: {case["name"]}')
        references.append(reference)
    eligible=[(case,reference) for case,reference in zip(cases,references)
              if json.loads(reference.with_suffix('.json').read_text())['segments']>=32]
    all_results={}
    for label,binary in candidates.items():
        results=[]
        for index,(case,reference) in enumerate(zip(cases,references)):
            folder=root/label/'fresh'/f'{index:03d}'
            invoke(binary,folder,'single',[case])
            results.append(compare(reference,folder/'w0-000',case,'fresh',0,index))
            print(label,'fresh',case['name'],'PASS',flush=True)
        modes=[] if args.fresh_only else (['single'] if args.skip_dual else ['single','dual'])
        for mode in modes:
            folder=root/label/mode
            invoke(binary,folder,mode,[case for case,reference in eligible])
            for worker in range(1 if mode=='single' else 2):
                for index,(case,reference) in enumerate(eligible):
                    results.append(compare(reference,folder/f'w{worker}-{index:03d}',case,mode,worker,index))
                    print(label,mode,worker,case['name'],'PASS',flush=True)
        (root/f'results-{label}.json').write_text(json.dumps(results,indent=2))
        all_results[label]={'comparisons':len(results),'inputs':len(cases),'gpu_route_inputs':len(eligible),
                            'independent_known_coefficient_inputs':len(stress),
                            'modes':{mode:sum(row['mode']==mode for row in results) for mode in ['fresh','single','dual']}}
        print(label,all_results[label],flush=True)
    after=provenance();(root/'provenance-after.json').write_text(json.dumps(after,indent=2))
    if before['files']!=after['files']:raise RuntimeError('Candidate/source/library identities changed during test')
    (root/'summary.json').write_text(json.dumps(all_results,indent=2))
    print('All exact coefficient and public-pixel comparisons passed; source/library/harness identities unchanged.')

if __name__=='__main__':main()
