"""Run identical JPEGs through fused GPUJPEG, a candidate and Fastvideo.

All runs return image bytes to host RAM and write one final PNM output. No
Fastvideo -discard flag is used. Application intervals have documented,
slightly different input boundaries; process wall time has a common boundary.
"""
from __future__ import annotations
import argparse, csv, datetime, hashlib, json, math, os, pathlib, re, statistics, subprocess, time

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def build_options(exe):
    cache=exe.parent/'CMakeCache.txt'
    if not cache.is_file(): return None
    selected={}
    for line in cache.read_text(encoding='utf-8',errors='replace').splitlines():
        if line.startswith(('HUFFMAN_GPU_','CMAKE_CUDA_ARCHITECTURES:','CMAKE_BUILD_TYPE:')) and '=' in line:
            key,value=line.split('=',1); selected[key]=value
    return selected

def choose_binaries(args,parser):
    label=args.candidate_label
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}',label) or label in ('baseline','fastvideo','old_staged'):
        parser.error('Candidate label must be a unique lowercase identifier up to32 characters')
    binaries={'baseline':args.baseline.resolve(),label:args.candidate.resolve(),'fastvideo':args.fastvideo.resolve()}
    if args.old_staged: binaries['old_staged']=args.old_staged.resolve()
    for item in args.extra:
        if '=' not in item: parser.error('--extra requires LABEL=EXECUTABLE_PATH')
        label,path=item.split('=',1)
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}',label) or label in binaries:
            parser.error('Extra labels must be unique lowercase identifiers up to32 characters')
        binaries[label]=pathlib.Path(path).resolve()
    if args.variant:
        if any(label not in binaries for label in args.variant): parser.error('--variant names an undefined binary')
        binaries={label:binaries[label] for label in args.variant}
    return binaries

def verify_integrity(metadata,binaries,cases,qpc_helper):
    items=[dict(role='binary',name=label,path=str(path),before_sha256=metadata['binaries'][label]['sha256']) for label,path in binaries.items()]
    items += [dict(role='input',name=c['name'],path=c['path'],before_sha256=c['sha256']) for c in cases]
    if qpc_helper:
        items.append(dict(role='observer',name='fastvideo_qpc',path=str(qpc_helper),before_sha256=metadata['fastvideo_qpc_helper']['sha256']))
    for item in items:
        path=pathlib.Path(item['path']); item['after_sha256']=digest(path); item['bytes']=path.stat().st_size
        item['match']=item['before_sha256']==item['after_sha256']
    return dict(all_match=all(x['match'] for x in items),entries=items,
                verified_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())

def stats(values):
    if not values:
        return None
    ordered=sorted(values)
    return dict(count=len(values),median_ms=statistics.median(values),mean_ms=statistics.mean(values),
                min_ms=min(values),max_ms=max(values),p95_ms=ordered[math.ceil(.95*len(ordered))-1])

def parse_log(kind,text,iterations,warmup):
    if kind=='fastvideo':
        decode=[float(v) for v in re.findall(r'^Decode time \(includes host-to-device transfer\) = ([\d.]+) ms',text,re.M)]
        copy=[float(v) for v in re.findall(r'^Device-To-Host transfer = ([\d.]+) ms',text,re.M)]
        load=[float(v) for v in re.findall(r'^JFIF load time from HDD to CPU memory = ([\d.]+) ms',text,re.M)]
        if len(decode)!=iterations or len(copy)!=iterations or len(load)!=iterations:
            raise ValueError(f'Fastvideo expected {iterations} rows per timer, got decode={len(decode)},D2H={len(copy)},load={len(load)}')
        reported=[d+c for d,c in zip(decode,copy)]
        plusload=[l+d+c for l,d,c in zip(load,decode,copy)]
        return dict(reported_transfer_inclusive_ms=reported,reported_with_container_load_ms=plusload,
                    memory_input_to_host_ms=plusload,warm_memory_input_to_host=stats(plusload[warmup:]),
                    primary_ms=plusload,warm_primary=stats(plusload[warmup:]),first_primary_ms=plusload[0],
                    decode_without_d2h_ms=decode,d2h_ms=copy,input_load_ms=load,
                    first_reported_ms=reported[0],warm_reported=stats(reported[warmup:]),
                    warm_with_container_load=stats(plusload[warmup:]),warm_d2h=stats(copy[warmup:]))
    decode=[float(v) for v in re.findall(r'^Decode Image:\s+([\d.]+) ms',text,re.M)]
    if len(decode)!=iterations:
        raise ValueError(f'GPUJPEG expected {iterations} Decode Image rows, got {len(decode)}')
    gpu=[float(v) for v in re.findall(r'^Decode Image GPU:\s+([\d.]+) ms',text,re.M)]
    return dict(reported_transfer_inclusive_ms=decode,first_reported_ms=decode[0],
                memory_input_to_host_ms=decode,warm_memory_input_to_host=stats(decode[warmup:]),
                primary_ms=decode,warm_primary=stats(decode[warmup:]),first_primary_ms=decode[0],
                warm_reported=stats(decode[warmup:]),diagnostic_gpu_reported_ms=gpu)

def pnm_info(path):
    b=path.read_bytes(); tokens=[]; p=0
    while len(tokens)<4:
        while p<len(b) and b[p] in b' \t\r\n': p+=1
        if p<len(b) and b[p]==35:
            p=b.index(b'\n',p)+1; continue
        start=p
        while p<len(b) and b[p] not in b' \t\r\n': p+=1
        tokens.append(b[start:p])
    if b[p:p+2]==b'\r\n': p+=2
    else: p+=1
    magic,width,height,maxval=tokens
    if magic not in (b'P5',b'P6') or int(maxval)!=255:
        raise ValueError(f'Expected 8-bit P5/P6 output, got {tokens}')
    channels=1 if magic==b'P5' else 3
    expected=int(width)*int(height)*channels
    if len(b)-p!=expected:
        raise ValueError(f'Output pixel-byte mismatch {len(b)-p} vs {expected}: {path}')
    return dict(width=int(width),height=int(height),channels=channels,pixel_bytes=expected,
                pixels_sha256=hashlib.sha256(b[p:]).hexdigest(),file_sha256=hashlib.sha256(b).hexdigest())

def apply_qpc_observations(timing,path,iterations,warmup):
    rows=list(csv.DictReader(path.open(newline=''),delimiter='\t'))
    if len(rows)!=iterations*2 or any(r['kind']!=('L' if i%2==0 else 'D') for i,r in enumerate(rows)):
        raise ValueError(f'Expected {iterations} alternating load/decode QPC pairs, got {len(rows)} rows')
    if len(set(r['frequency'] for r in rows))!=1 or len(set(r['thread'] for r in rows))!=1:
        raise ValueError('QPC records must have one positive frequency and one thread')
    if os.name=='nt':
        import ctypes
        frequency=ctypes.c_longlong()
        if not ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(frequency)) or int(rows[0]['frequency'])!=frequency.value:
            raise ValueError('Captured timer frequency differs from Windows QueryPerformanceFrequency')
    for r in rows:
        if int(r['frequency'])<=0 or int(r['stop'])<int(r['start']):
            raise ValueError('Invalid QPC counter/frequency record')
    loads=[1000*(int(r['stop'])-int(r['start']))/int(r['frequency']) for r in rows if r['kind']=='L']
    decodes=[1000*(int(r['stop'])-int(r['start']))/int(r['frequency']) for r in rows if r['kind']=='D']
    corrected=[]; gaps=[]
    for load,decode in zip(rows[::2],rows[1::2]):
        if not int(load['start'])<=int(load['stop'])<=int(decode['start'])<=int(decode['stop']):
            raise ValueError('Non-monotonic paired header/decode QPC interval')
        frequency=int(load['frequency'])
        corrected.append(1000*(int(decode['stop'])-int(load['start']))/frequency)
        gaps.append(1000*(int(decode['start'])-int(load['stop']))/frequency)
    timing.setdefault('invalid_printed_load_plus_codec_ms',timing['reported_with_container_load_ms'])
    timing.update(primary_ms=corrected,warm_primary=stats(corrected[warmup:]),first_primary_ms=corrected[0],
                  memory_input_to_host_ms=corrected,warm_memory_input_to_host=stats(corrected[warmup:]),
                  qpc_load_ms=loads,qpc_decode_to_host_ms=decodes,
                  qpc_two_interval_sum_ms=[l+d for l,d in zip(loads,decodes)],qpc_intervening_gap_ms=gaps,
                  warm_qpc_intervening_gap=stats(gaps[warmup:]),
                  cached_initial_disk_charge_estimate_ms=[l-q for l,q in zip(timing['input_load_ms'],loads)],
                  primary_source='Version-bound own-process QPC IAT observation: continuous header-load START through codec completed-download STOP, including the intervening host gap; cached initial disk charge excluded.')
    return timing

def command(kind,exe,case,output,iterations):
    if kind=='fastvideo':
        return [str(exe),'-i',case['path'],'-o',str(output),'-repeat',str(iterations),'-thread','1','-info']
    cmd=[str(exe),'-d','-n',str(iterations),'-v']
    cmd+=['-f','u8'] if case['channels']==1 else ['-c','rgb','-f','444-u8-p012']
    return cmd+[case['path'],str(output)]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True,type=pathlib.Path)
    parser.add_argument('--baseline',required=True,type=pathlib.Path)
    parser.add_argument('--candidate','--staged',required=True,type=pathlib.Path)
    parser.add_argument('--candidate-label',default='candidate',help='Candidate result label; use staged for compatibility with prior reports')
    parser.add_argument('--fastvideo',required=True,type=pathlib.Path)
    parser.add_argument('--fastvideo-qpc-helper',type=pathlib.Path,default=pathlib.Path(__file__).with_name('qpc_capture.dll'),help='Version-bound QPC observer DLL, required when Fastvideo is selected')
    parser.add_argument('--old-staged',type=pathlib.Path,help='Optional previous staged build for ablation runs')
    parser.add_argument('--extra',action='append',default=[],help='Optional ablation LABEL=EXECUTABLE_PATH')
    parser.add_argument('--variant',action='append',default=[],help='Select labels/order for a shorter ablation run; default is every supplied variant')
    parser.add_argument('--output',required=True,type=pathlib.Path)
    parser.add_argument('--rounds',type=int,default=5)
    parser.add_argument('--iterations',type=int,default=110)
    parser.add_argument('--warmup',type=int,default=10)
    parser.add_argument('--case',action='append',default=[])
    parser.add_argument('--timeout',type=float,default=180)
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    if not 0<=args.warmup<args.iterations or args.rounds<1:
        parser.error('Need 0<=warmup<iterations and rounds>=1')
    manifest=json.loads(args.manifest.read_text(encoding='utf-8-sig'))
    if isinstance(manifest,dict): manifest=manifest.get('cases',manifest.get('images',manifest))
    cases=[dict(c) for c in manifest if not args.case or c['name'] in args.case]
    if not cases: parser.error('No selected cases')
    binaries=choose_binaries(args,parser)
    qpc_helper=args.fastvideo_qpc_helper.resolve() if 'fastvideo' in binaries else None
    if qpc_helper:
        if not qpc_helper.is_file(): parser.error('QPC observer DLL missing')
        if digest(binaries['fastvideo'])!='01735bf880587184119d70d56bda433ef01516e8d1139d880c765afca34dbac3':
            parser.error('QPC observer supports only the investigated Fastvideo binary SHA256')
    for label,p in binaries.items():
        if not p.is_file(): parser.error(f'Missing {label} binary: {p}')
    for c in cases:
        p=pathlib.Path(c['path'])
        if not p.is_absolute(): p=args.manifest.parent/p
        c['path']=str(p.resolve())
        if digest(p)!=c['sha256']: raise ValueError(f'Input digest differs from manifest: {p}')
    output=args.output.resolve(); output.mkdir(parents=True,exist_ok=True)
    (output/'logs').mkdir(exist_ok=True); (output/'images').mkdir(exist_ok=True)
    labels=list(binaries); plan=[]
    for roundno in range(args.rounds):
        order=labels[roundno%len(labels):]+labels[:roundno%len(labels)]
        # Alternate image order too, reducing monotonic clock/thermal bias.
        for c in cases if roundno%2==0 else reversed(cases):
            for label in order:
                ext='.pgm' if c['channels']==1 else '.ppm'
                image=output/'images'/(c['name']+'-'+label+ext)
                plan.append(dict(round=roundno,case=c['name'],variant=label,output=str(image),
                                 command=command(label,binaries[label],c,image,args.iterations)))
    metadata=dict(created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  manifest_sha256=digest(args.manifest),inputs=cases,
                  binaries={k:dict(path=str(v),sha256=digest(v),cmake_options=build_options(v)) for k,v in binaries.items()},
                  rounds=args.rounds,iterations=args.iterations,warmup=args.warmup,
                  candidate_label=args.candidate_label,
                  candidate_description='Selected experimental GPUJPEG binary; the label is an alias, and recorded CMake options identify the actual compact/unstuff feature configuration.',
                  fastvideo_qpc_helper=dict(path=str(qpc_helper),sha256=digest(qpc_helper)) if qpc_helper else None,
                  boundary_notes={
                      'primary':'With the QPC helper: CONTINUOUS actual Fastvideo memory-stream header-load QPC START through codec completed-download QPC STOP vs GPUJPEG Decode Image. This includes the intervening host gap and excludes the cached initial disk-read time that Fastvideo adds to every printed JFIF-load value. Without the QPC helper Fastvideo printed load+codec is INVALID as per-frame latency.',
                      'GPUJPEG_reported':'Host wall time: JPEG parsing, decoder work, H2D and synchronous output D2H; no input/output file I/O.',
                      'Fastvideo_reported':'Printed Decode time + Device-To-Host time. Native QPC brackets SDK decode, processing and synchronous output download, excludes JPEG header/SOS loading. Includes no-RST host entropy transcode.',
                      'Fastvideo_plus_load':'INVALID for per-frame latency: the printed JFIF-load value equals actual per-repeat memory parsing/copy time plus a cached one-time initial disk-load charge. Native bytes add node+0x6c at0x1400072fc. Retained only as a diagnostic; use direct QPC observations for the complete-memory primary interval.',
                      'process_wall':'Python perf_counter around child execution until process exit: initialization, all iterations, input parsing/I/O, final output write, console printing and cleanup. Wall/iterations amortizes these costs; first iterations remain included.',
                      'warmup':'Discard the first warmup samples within each process, independently per variant.',
                      'output':'Returned CPU image bytes and final P5/P6 output required. No Fastvideo -discard. Output channel count and pixel-byte size checked.',
                      'environment':'Sequential child runs, rotating variant order; corpus SHA256 verified. File-cache warmness uncontrolled beyond reading/hash-checking every input before runs. No GPU clock locking.'},plan=plan)
    (output/'plan.json').write_text(json.dumps(metadata,indent=2))
    if args.dry_run:
        print(json.dumps(dict(planned_runs=len(plan),output=str(output)),indent=2)); return
    rows=[]; env=os.environ.copy()
    # Deliberately remove profiler injection from child benchmark environments.
    env.pop('CUDA_INJECTION64_PATH',None); env.pop('FASTJPEG_CAPTURE_DIR',None)
    cases_by_name={c['name']:c for c in cases}
    for task in plan:
        label=task['variant']; c=cases_by_name[task['case']]
        image=pathlib.Path(task['output'])
        if image.exists(): image.unlink()
        child_env=env.copy()
        qpc_log=output/'logs'/f"{c['name']}-r{task['round']}-{label}.qpc.tsv"
        if label=='fastvideo' and qpc_helper:
            child_env['CUDA_INJECTION64_PATH']=str(qpc_helper)
            child_env['FASTJPEG_QPC_LOG']=str(qpc_log)
            if qpc_log.exists(): qpc_log.unlink()
        start=time.perf_counter_ns()
        proc=subprocess.run(task['command'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                            env=child_env,timeout=args.timeout,check=False)
        elapsed_ms=(time.perf_counter_ns()-start)/1e6
        log=output/'logs'/f"{c['name']}-r{task['round']}-{label}.txt"; log.write_bytes(proc.stdout)
        text=proc.stdout.decode('utf-8',errors='replace').replace('\r\n','\n')
        row=dict(round=task['round'],case=c['name'],variant=label,returncode=proc.returncode,
                 process_wall_ms=elapsed_ms,process_wall_per_iteration_ms=elapsed_ms/args.iterations,
                 log=str(log),command=task['command'])
        try:
            if proc.returncode: raise RuntimeError(f'Process exit {proc.returncode}')
            row['timing']=parse_log(label,text,args.iterations,args.warmup)
            if label=='fastvideo':
                row['primary_valid']=bool(qpc_helper)
                if qpc_helper:
                    row['timing']=apply_qpc_observations(row['timing'],qpc_log,args.iterations,args.warmup)
                    row['qpc_log']=str(qpc_log)
            else: row['primary_valid']=True
            row['output_image']=pnm_info(image)
            if any(row['output_image'][k]!=c[k] for k in ('width','height','channels')):
                raise ValueError('Output shape does not match input manifest')
            row['status']='ok'
        except Exception as e:
            row['status']='failed'; row['error']=str(e)
        rows.append(row)
        (output/'runs.json').write_text(json.dumps(rows,indent=2))
        med=row.get('timing',{}).get('warm_primary',{}).get('median_ms')
        print(f"round={task['round']+1} {c['name']} {label}: {row['status']}, primary_warm_median={med}ms, process={elapsed_ms:.2f}ms",flush=True)
        if row['status']!='ok':
            raise RuntimeError(f"Benchmark failed; see {log}: {row['error']}")
    summaries=[]
    for c in cases:
        for label in labels:
            selected=[r for r in rows if r['case']==c['name'] and r['variant']==label]
            samples=[x for r in selected for x in r['timing']['reported_transfer_inclusive_ms'][args.warmup:]]
            item=dict(case=c['name'],variant=label,warm_transfer_inclusive=stats(samples),
                      primary_valid=all(r['primary_valid'] for r in selected),
                      warm_primary=stats([x for r in selected for x in r['timing']['primary_ms'][args.warmup:]]),
                      primary_round_medians_ms=[r['timing']['warm_primary']['median_ms'] for r in selected],
                      primary_median_round_ms=statistics.median(r['timing']['warm_primary']['median_ms'] for r in selected),
                      warm_memory_input_to_host=stats([x for r in selected for x in r['timing']['memory_input_to_host_ms'][args.warmup:]]),
                      median_round_memory_input_to_host_ms=statistics.median(r['timing']['warm_memory_input_to_host']['median_ms'] for r in selected),
                      round_medians_ms=[r['timing']['warm_reported']['median_ms'] for r in selected],
                      median_round_ms=statistics.median(r['timing']['warm_reported']['median_ms'] for r in selected),
                      process_wall_per_iteration=stats([r['process_wall_per_iteration_ms'] for r in selected]),
                      output_pixel_hashes=sorted(set(r['output_image']['pixels_sha256'] for r in selected)))
            if label=='fastvideo':
                item['warm_with_container_load']=stats([x for r in selected for x in r['timing']['reported_with_container_load_ms'][args.warmup:]])
            summaries.append(item)
    (output/'summary.json').write_text(json.dumps(summaries,indent=2))
    if any(digest(pathlib.Path(c['path']))!=c['sha256'] for c in cases):
        raise RuntimeError('Corpus input mutated during benchmark')
    if any(digest(path)!=metadata['binaries'][label]['sha256'] for label,path in binaries.items()):
        raise RuntimeError('A benchmark binary changed during execution')
    if qpc_helper and digest(qpc_helper)!=metadata['fastvideo_qpc_helper']['sha256']:
        raise RuntimeError('QPC observer DLL changed during execution')
    integrity=verify_integrity(metadata,binaries,cases,qpc_helper)
    (output/'integrity-verification.json').write_text(json.dumps(integrity,indent=2))
    if not integrity['all_match']: raise RuntimeError('Integrity verification failed')
    print(f'Completed {len(rows)} sequential runs. Results: {output}',flush=True)

if __name__=='__main__': main()
