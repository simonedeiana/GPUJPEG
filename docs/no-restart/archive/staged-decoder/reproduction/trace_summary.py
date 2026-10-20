"""Summarize a common transfer-complete window from an Nsight Systems SQLite.

Window = first H2D activity after the prior output download through completion
of the full output D2H activity. This includes both transfers and GPU/host gaps
between them, but excludes CPU work before the first upload and file I/O.
It is a diagnostic window, not full codec API latency.
"""
import argparse, hashlib, json, pathlib, sqlite3, statistics

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sqlite',required=True,type=pathlib.Path)
    p.add_argument('--output-bytes',required=True,type=int)
    p.add_argument('--payload-min-bytes',default=0,type=int,help='Optional explicit minimum H2D copy size to identify the image/coefficients payload; smaller metadata uploads are excluded from window start')
    p.add_argument('--warmup',default=10,type=int)
    p.add_argument('--output',required=True,type=pathlib.Path)
    args=p.parse_args()
    c=sqlite3.connect(f'file:{args.sqlite.resolve().as_posix()}?mode=ro',uri=True)
    c.row_factory=sqlite3.Row
    copies=[dict(r) for r in c.execute('SELECT start,end,bytes,copyKind,correlationId,streamId FROM CUPTI_ACTIVITY_KIND_MEMCPY ORDER BY start')]
    outputs=[r for r in copies if r['copyKind']==2 and r['bytes']==args.output_bytes]
    if not outputs: raise ValueError('No output-sized D2H activity; cannot establish completed-image transfer window')
    rows=[]; prev=0
    for i,o in enumerate(outputs):
        h=[r for r in copies if r['copyKind']==1 and prev<=r['start']<o['start']]
        if not h: raise ValueError(f'Output {i} has no preceding H2D; cannot establish complete input window')
        payloads=[r for r in h if r['bytes']>=args.payload_min_bytes]
        if not payloads: raise ValueError(f'Output {i} has no H2D matching declared payload minimum {args.payload_min_bytes}')
        first=payloads[0]
        kernels=[dict(r) for r in c.execute('SELECT k.start,k.end,s.value name FROM CUPTI_ACTIVITY_KIND_KERNEL k JOIN StringIds s ON s.id=k.demangledName WHERE k.start>=? AND k.end<=? ORDER BY k.start',(first['start'],o['end']))]
        api=c.execute('SELECT r.start,r.end,s.value name,r.globalTid FROM CUPTI_ACTIVITY_KIND_RUNTIME r JOIN StringIds s ON s.id=r.nameId WHERE r.correlationId=?',(o['correlationId'],)).fetchone()
        # On an asynchronous output API, the first later synchronization whose
        # completed host interval follows device D2H end provides corroboration.
        sync=c.execute("SELECT r.start,r.end,s.value name,r.globalTid FROM CUPTI_ACTIVITY_KIND_RUNTIME r JOIN StringIds s ON s.id=r.nameId WHERE r.start>=? AND r.end>=? AND (s.value LIKE '%Synchronize%') ORDER BY r.end LIMIT 1",(api['start'] if api else o['start'],o['end'])).fetchone()
        rows.append(dict(iteration=i,window_ms=(o['end']-first['start'])/1e6,
                         first_h2d_ns=first['start'],output_d2h_start_ns=o['start'],output_d2h_end_ns=o['end'],
                         h2d_count=len(h),h2d_bytes=sum(r['bytes'] for r in h),d2h_output_bytes=o['bytes'],
                         payload_start_h2d_bytes=first['bytes'],payload_start_h2d_correlation=first['correlationId'],
                         output_api=dict(api) if api else None,next_completion_sync=dict(sync) if sync else None,
                         kernel_count=len(kernels),kernel_ms_sum=sum(k['end']-k['start'] for k in kernels)/1e6,
                         kernel_names=[k['name'] for k in kernels]))
        prev=o['end']
    warm=rows[args.warmup:]
    if not warm: raise ValueError(f'Only {len(rows)} frames, warmup={args.warmup}')
    vals=[r['window_ms'] for r in warm]
    result=dict(sqlite=str(args.sqlite.resolve()),sqlite_sha256=hashlib.sha256(args.sqlite.read_bytes()).hexdigest(),
                boundary='First payload-sized H2D device activity after prior output, through full raw-output D2H completion; excludes CPU preprocessing before that H2D.',
                output_bytes=args.output_bytes,payload_min_bytes=args.payload_min_bytes,frames=len(rows),warmup=args.warmup,
                warm_median_ms=statistics.median(vals),warm_mean_ms=statistics.mean(vals),
                warm_min_ms=min(vals),warm_max_ms=max(vals),rows=rows)
    args.output.write_text(json.dumps(result,indent=2)); print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2))

if __name__=='__main__': main()
