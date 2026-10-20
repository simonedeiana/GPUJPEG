"""Same-instance layout changes and two-stream/two-host-thread overlap checks."""
from pathlib import Path
import argparse,json,subprocess
from jpeg_checks import JPEG
from validate_encode import environment,sha

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--reference',type=Path,required=True)
    p.add_argument('--candidate',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();root=args.output.resolve();root.mkdir(parents=True,exist_ok=True)
    inputs={x['name']:x for x in json.loads(args.manifest.read_text())['inputs']}
    # Grow CPU/GPU capacities from tiny to maximum before mixed fallback transitions.
    # This avoids an unrelated upstream CPU resize defect after a large GPU-only configuration.
    sequence=[('tiny-black-gray','gray',100,1,0),('4k-rgb','444',90,1,0),('tiny-white','444',100,1,0),('hd-rgb','420',75,1,10),
        ('hd-gray','gray',90,1,0),('odd-rgb','422',100,0,0),('noise-rgb','420',100,1,10),
        ('checker-gray','gray',1,1,0),('4k-gray','gray',90,1,0),('4k-rgb','420',75,0,0),
        ('odd-rgb','420',90,1,1),('noise-rgb','444',100,0,0),('hd-rgb','444',90,1,8),('hd-rgb','444',90,1,0)]
    # Both are RI0 with different dimensions, quality and sampling; preinitialized before host threads overlap.
    concurrent=[('hd-rgb','420',75,1,0),('4k-rgb','444',100,1,0)]
    summary={'provenance':{label:sha(binary) for label,binary in [('reference',args.reference),('candidate',args.candidate)]},'tests':[]}
    for mode,cases in [('reuse',sequence),('concurrent',concurrent)]:
        manifest=root/(mode+'.tsv');lines=[]
        for index,(name,sampling,q,interleaved,restart) in enumerate(cases):
            item=inputs[name]
            lines.append(f'{index}-{name} '+json.dumps(item['raw'])+f' {item["width"]} {item["height"]} {item["channels"]} {sampling} {q} {interleaved} {restart}')
        manifest.write_text('\n'.join(lines)+'\n')
        if mode=='concurrent':
            groundtruth=root/mode/'reference-sequential';groundtruth.mkdir(parents=True,exist_ok=True)
            run=subprocess.run([str(args.reference.resolve()),str(manifest),str(groundtruth)],capture_output=True,timeout=180,env=environment())
            (groundtruth/'stdout.log').write_bytes(run.stdout);(groundtruth/'stderr.log').write_bytes(run.stderr)
            if run.returncode:raise RuntimeError(f'Sequential concurrency ground truth failed: {run.returncode}')
        for label,binary in [('reference',args.reference),('candidate',args.candidate)]:
            out=root/mode/label;out.mkdir(parents=True,exist_ok=True)
            command=[str(binary.resolve()),str(manifest),str(out)]+(['--concurrent'] if mode=='concurrent' else [])
            run=subprocess.run(command,capture_output=True,timeout=180,env=environment())
            (out/'stdout.log').write_bytes(run.stdout);(out/'stderr.log').write_bytes(run.stderr)
            if run.returncode:raise RuntimeError(f'{mode} {label} failed: {run.returncode}')
        expected=len(cases)*(5 if mode=='concurrent' else 2)
        names=sorted(x.name for x in (root/mode/'reference').glob('*.jpg'))
        if len(names)!=expected:raise AssertionError('Wrong state test output count')
        for name in names:
            ref=(root/mode/'reference'/name).read_bytes();candidate=(root/mode/'candidate'/name).read_bytes()
            case_index=int(name.split('-')[0]);requested=cases[case_index][-1]
            audit=JPEG(candidate).audit(require_ri0=requested==0)
            if ref!=candidate:raise AssertionError(f'State byte mismatch {mode} {name}')
            if mode=='concurrent':
                expected_name=name.rsplit('-',1)[0]+'-0.jpg'
                if candidate!=(root/mode/'reference-sequential'/expected_name).read_bytes():
                    raise AssertionError(f'Concurrent output differs from isolated sequential original {name}')
            summary['tests'].append({'mode':mode,'name':name,'byte_identical':True,'restart_requested':requested,'audit':audit})
            print(mode,name,'PASS',flush=True)
    summary['reuse_cases']=len(sequence)*2;summary['concurrent_cases']=10
    (root/'summary.json').write_text(json.dumps(summary,indent=2));print(f'PASS {len(sequence)*2} reuse outputs + 10 concurrent outputs')
if __name__=='__main__':main()
