"""Randomized encoder whole-call comparison, including host input/output transfers."""
from pathlib import Path
import argparse,json,random,statistics,subprocess
from jpeg_checks import JPEG,quality
from validate_encode import environment,sha

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True);p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--candidate',type=Path,required=True);p.add_argument('--nvjpeg',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--pillow-path',type=Path)
    p.add_argument('--rounds',type=int,default=5);p.add_argument('--iterations',type=int,default=110)
    p.add_argument('--warmup',type=int,default=10);p.add_argument('--seed',type=int,default=20261020)
    p.add_argument('--identity',action='append',default=[],help='LABEL=PATH extra source/library/DLL hashes')
    args=p.parse_args();root=args.output.resolve();root.mkdir(parents=True,exist_ok=True)
    manifest=json.loads(args.manifest.read_text());inputs={x['name']:x for x in manifest['inputs']}
    binaries={label:getattr(args,label).resolve() for label in ['baseline','candidate','nvjpeg']}
    identities={**binaries,**dict(x.split('=',1) for x in args.identity)}
    before={label:sha(path) for label,path in identities.items()}
    for item in inputs.values():
        if sha(item['raw'])!=item['raw_sha256']:raise RuntimeError('Raw identity changed')
    raw_before={name:sha(item['raw']) for name,item in inputs.items()}
    rng=random.Random(args.seed);schedule=[];previous=None
    for r in range(args.rounds):
        cases=manifest['benchmark_q90'][:];rng.shuffle(cases)
        for case in cases:
            order=list(binaries);rng.shuffle(order)
            if order[0]==previous:order=order[1:]+order[:1]
            schedule.extend({'round':r,'case':case,'encoder':label} for label in order);previous=order[-1]
    config={'seed':args.seed,'rounds':args.rounds,'iterations':args.iterations,'warmup':args.warmup,
            'identity_sha256':before,'identity_paths':{k:str(v) for k,v in identities.items()},'binaries':{k:str(v) for k,v in binaries.items()},'schedule':schedule,
            'raw_input_sha256':raw_before,
            'timer_scope':'Host packed input H2D + encoding + complete compressed HOST retrieval + stream synchronization; no file IO, no stage instrumentation',
            'host_memory':'pageable input and output','input_manifest_sha256':sha(args.manifest)}
    (root/'configuration.json').write_text(json.dumps(config,indent=2));results=[];metrics={};case_outputs={}
    for index,job in enumerate(schedule):
        case=job['case'];item=inputs[case['input']];label=job['encoder']
        stem=f'{index:03d}-r{job["round"]}-{case["name"]}-{label}';output=root/(stem+'.jpg')
        command=[str(binaries[label]),'--input',item['raw'],'--width',str(item['width']),'--height',str(item['height']),
            '--channels',str(item['channels']),'--sampling',case['sampling'],'--quality',str(case['quality']),
            '--optimized','0','--pinned','0','--iterations',str(args.iterations),'--warmup',str(args.warmup),'--output',str(output)]
        if label!='nvjpeg':command.extend(['--interleaved','1'])
        run=subprocess.run(command,capture_output=True,timeout=240,env=environment())
        (root/(stem+'.stdout.log')).write_bytes(run.stdout);(root/(stem+'.stderr.log')).write_bytes(run.stderr)
        if run.returncode:raise RuntimeError(f'Benchmark encoder failed {stem}: {run.returncode}')
        timing=json.loads(run.stdout);audit=JPEG(output.read_bytes()).audit(require_ri0=True)
        if (audit['width'],audit['height'])!=(item['width'],item['height']):raise AssertionError('Wrong dimensions')
        if audit['sha256'] not in metrics:metrics[audit['sha256']]=quality(output,item,args.pillow_path)
        entry={'index':index,**job,'timing':timing,'audit':audit,'source_quality':metrics[audit['sha256']]}
        results.append(entry);case_outputs.setdefault(case['name'],{}).setdefault(label,[]).append(entry)
        (root/'results.json').write_text(json.dumps(results,indent=2))
        print(stem,timing['warm_primary']['median_ms'],'ms',audit['bytes'],'bytes',flush=True)
    summaries=[]
    for name,vendors in case_outputs.items():
        row={'case':name,'encoders':{}}
        for label,runs in vendors.items():
            medians=[x['timing']['warm_primary']['median_ms'] for x in runs]
            row['encoders'][label]={'median_of_round_medians_ms':statistics.median(medians),'round_medians_ms':medians,
                'min_round_median_ms':min(medians),'max_round_median_ms':max(medians),
                'output_bytes':runs[0]['audit']['bytes'],'source_quality':runs[0]['source_quality'],
                'stable_jpeg_bytes_across_rounds':len({x['audit']['sha256'] for x in runs})==1}
        ref=vendors['baseline'][0];new=vendors['candidate'][0];nv=vendors['nvjpeg'][0]
        row['candidate_byte_identical_to_baseline']=all(x['audit']['sha256']==ref['audit']['sha256'] for x in vendors['candidate'])
        row['nvjpeg_component_quantizers_equal_to_baseline']=nv['audit']['component_quantization_tables']==ref['audit']['component_quantization_tables']
        if not row['candidate_byte_identical_to_baseline']:raise AssertionError('Candidate benchmark output byte mismatch')
        row['speedup_original_over_candidate']=row['encoders']['baseline']['median_of_round_medians_ms']/row['encoders']['candidate']['median_of_round_medians_ms']
        row['candidate_over_nvjpeg_latency_ratio']=row['encoders']['candidate']['median_of_round_medians_ms']/row['encoders']['nvjpeg']['median_of_round_medians_ms']
        summaries.append(row)
    after={label:sha(path) for label,path in identities.items()}
    if before!=after:raise RuntimeError('Benchmark binaries changed')
    raw_after={name:sha(item['raw']) for name,item in inputs.items()}
    if raw_before!=raw_after:raise RuntimeError('Raw input bytes changed')
    (root/'provenance-after.json').write_text(json.dumps(after,indent=2))
    (root/'raw-input-provenance-after.json').write_text(json.dumps(raw_after,indent=2))
    (root/'summary.json').write_text(json.dumps(summaries,indent=2));print('DONE',len(results),'encoder processes')
if __name__=='__main__':main()
