"""Collect serial Nsight CUDA traces on the same exact core-case JPEGs.

Run only after ordinary, unprofiled benchmark execution and GPU coordination.
Profiling perturbs timing; these traces corroborate transfer counts/boundaries.
"""
import argparse,json,os,pathlib,re,subprocess,sys
from benchmark import command,digest,choose_binaries,build_options
w=pathlib.Path(__file__).resolve().parent

def entropy_transport_bytes(path):
    """Sequential test-corpus entropy bytes excluding RST/EOI, keeping FF00.

    GPUJPEG removes restart markers before upload; Fastvideo uploads them too.
    This exact lower size admits either payload and excludes small metadata.
    """
    data=pathlib.Path(path).read_bytes()
    if data[:2]!=b'\xff\xd8': raise ValueError('Expected JPEG SOI')
    offset=2
    while True:
        if data[offset]!=255: raise ValueError('Invalid JPEG header marker')
        marker=data[offset+1]; length=int.from_bytes(data[offset+2:offset+4],'big')
        offset+=2+length
        if marker==0xda: break
    count=0; i=offset
    while i<len(data):
        if data[i]!=255: count+=1; i+=1; continue
        marker=data[i+1]
        if marker==0: count+=2; i+=2
        elif 0xd0<=marker<=0xd7: i+=2
        elif marker==0xd9: return count
        else: raise ValueError(f'Unexpected entropy marker {marker:#x}; this corpus selector handles one sequential scan')
    raise ValueError('JPEG EOI missing')
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--manifest',required=True,type=pathlib.Path)
p.add_argument('--baseline',required=True,type=pathlib.Path)
p.add_argument('--candidate','--staged',required=True,type=pathlib.Path)
p.add_argument('--candidate-label',default='candidate')
p.add_argument('--fastvideo',required=True,type=pathlib.Path)
p.add_argument('--old-staged',type=pathlib.Path)
p.add_argument('--extra',action='append',default=[])
p.add_argument('--variant',action='append',default=[])
p.add_argument('--require-candidate-kernel',action='append',default=[],help='Regex that must match a kernel in EVERY warm candidate frame')
p.add_argument('--require-kernel',action='append',default=[],help='LABEL=REGEX required on EVERY warm frame of that variant')
p.add_argument('--nsys',required=True,type=pathlib.Path)
p.add_argument('--output',required=True,type=pathlib.Path)
p.add_argument('--case',action='append',default=[])
p.add_argument('--iterations',type=int,default=30)
p.add_argument('--warmup',type=int,default=10)
p.add_argument('--dry-run',action='store_true')
args=p.parse_args()
selected=args.case or ['hd444-r10','hd420-r10','4k444-r10','4k420-r10']
manifest=json.loads(args.manifest.read_text(encoding='utf-8-sig'))
if isinstance(manifest,dict): manifest=manifest.get('cases',manifest.get('images',manifest))
cases=[dict(c) for c in manifest if c['name'] in selected]
if set(c['name'] for c in cases)!=set(selected):
    raise ValueError('One or more requested cases are absent from the manifest')
for c in cases:
    image_path=pathlib.Path(c['path'])
    if not image_path.is_absolute(): image_path=args.manifest.parent/image_path
    c['path']=str(image_path.resolve())
out=args.output.resolve(); out.mkdir(parents=True,exist_ok=True)
bins=choose_binaries(args,p)
required={label:[] for label in bins}
if args.candidate_label in required: required[args.candidate_label]+=args.require_candidate_kernel
for item in args.require_kernel:
    if '=' not in item: p.error('--require-kernel requires LABEL=REGEX')
    label,pattern=item.split('=',1)
    if label not in required: p.error('--require-kernel names an unselected binary')
    re.compile(pattern); required[label].append(pattern)
binary_hashes={label:digest(exe) for label,exe in bins.items()}
for c in cases:
    if digest(pathlib.Path(c['path']))!=c['sha256']: raise ValueError('Corpus hash mismatch: '+c['name'])
commands=[]; reports=[]
env=os.environ.copy(); env.pop('CUDA_INJECTION64_PATH',None); env.pop('FASTJPEG_CAPTURE_DIR',None)
for c in cases:
    for variant,exe in bins.items():
        stem=out/(c['name']+'-'+variant)
        image=stem.with_suffix('.pgm' if c['channels']==1 else '.ppm')
        cmd=[str(args.nsys.resolve()),'profile','--trace=cuda','--sample=none','--force-overwrite=true','-o',str(stem)]+command(variant,exe,c,image,args.iterations)
        commands.append(cmd)
        if args.dry_run: continue
        print('Profiling',c['name'],variant,flush=True)
        with stem.with_suffix('.profile.log').open('wb') as f:
            subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,env=env,check=True,timeout=180)
        report=stem.with_suffix('.nsys-rep'); db=stem.with_suffix('.sqlite')
        subprocess.run([str(args.nsys.resolve()),'export','--type=sqlite','--force-overwrite=true','--output',str(db),str(report)],stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT,check=True,timeout=120)
        threshold=entropy_transport_bytes(c['path'])
        summary=stem.with_suffix('.window.json')
        subprocess.run([sys.executable,str(w/'trace_summary.py'),'--sqlite',str(db),'--output-bytes',str(c['expected_output_bytes']),
                        '--payload-min-bytes',str(threshold),'--warmup',str(args.warmup),'--output',str(summary)],check=True)
        r=json.loads(summary.read_text()); r.update(case=c['name'],variant=variant,input_sha256=c['sha256'],binary_sha256=digest(exe))
        if r['frames']!=args.iterations:
            raise RuntimeError(f'Expected {args.iterations} full raw-output D2H copies, got {r["frames"]}')
        if required[variant]:
            for pattern in required[variant]:
                missing=[row['iteration'] for row in r['rows'][args.warmup:] if not any(re.search(pattern,name) for name in row['kernel_names'])]
                if missing: raise RuntimeError(f'Required candidate kernel {pattern!r} missing in frames {missing}')
            r['required_kernel_patterns']=required[variant]
        reports.append(r); (out/'transfer-windows.json').write_text(json.dumps(reports,indent=2))
        compact=[dict(case=report['case'],variant=report['variant'],binary_sha256=report['binary_sha256'],
                      frames=report['frames'],warmup=report['warmup'],warm_median_window_ms=report['warm_median_ms'],
                      boundary=report['boundary'],kernels=report['kernel_summary']) for report in reports]
        (out/'kernel-summary.json').write_text(json.dumps(compact,indent=2))
(out/'commands.json').write_text(json.dumps(commands,indent=2))
if any(digest(exe)!=binary_hashes[label] for label,exe in bins.items()):
    raise RuntimeError('A profiled binary changed during execution')
integrity=dict(all_match=True,binaries={label:dict(path=str(exe),before_sha256=binary_hashes[label],after_sha256=digest(exe),cmake_options=build_options(exe)) for label,exe in bins.items()},
               inputs={case['name']:dict(before_sha256=case['sha256'],after_sha256=digest(pathlib.Path(case['path']))) for case in cases})
integrity['all_match']=all(v['before_sha256']==v['after_sha256'] for group in ('binaries','inputs') for v in integrity[group].values())
(out/'integrity-verification.json').write_text(json.dumps(integrity,indent=2))
if not integrity['all_match']: raise RuntimeError('Profile integrity verification failed')
print('Profile commands',len(commands),'written to',out,flush=True)
