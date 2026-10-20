"""CPU-only category summaries from retained, per-frame nvJPEG CUDA traces."""
import json,pathlib,sqlite3,statistics
w=pathlib.Path(__file__).resolve().parent/'profiles'
reports=json.loads((w/'profiles.json').read_text()); result=[]
for report in reports:
    c=sqlite3.connect(w/(report['case']+'.sqlite')); c.row_factory=sqlite3.Row
    copies=[dict(r) for r in c.execute('SELECT start,end,bytes,copyKind,correlationId FROM CUPTI_ACTIVITY_KIND_MEMCPY ORDER BY start')]
    frame_rows=[]
    for frame in report['frames']:
        assembled=False; stages={name:0.0 for name in ('color','dct','ac','dc','bit_offset_scan','assemble','stuff_length_scan','stuff')}
        for k in frame['kernels']:
            name=k['name']; category=None
            if 'format_to_ycbcr_kernel' in name: category='color'
            elif 'forwardDct' in name: category='dct'
            elif 'encoding::ACKernel' in name: category='ac'
            elif 'encoding::DCKernel' in name: category='dc'
            elif 'OutputOffsetKernel' in name: category='bit_offset_scan'
            elif 'BlockAssembleKernel' in name: category='assemble'; assembled=True
            elif 'BlockLengthKernel' in name: category='stuff_length_scan'
            elif 'ByteStuffingKernel' in name: category='stuff'
            elif 'SumBlocksKernel' in name or 'UpdateBlocksKernel' in name: category='stuff_length_scan' if assembled else 'bit_offset_scan'
            if category: stages[category]+=(k['end']-k['start'])/1e6
            else: raise ValueError('Unclassified actual kernel '+name)
        end=max(d['end'] for d in frame['downloads'])
        ups=[copy for copy in copies if copy['copyKind']==1 and frame['raw_upload']['start']<=copy['start']<end]
        downs=frame['downloads']
        frame_rows.append(dict(iteration=frame['iteration'],kernel_stages_ms=stages,
                              entropy_kernel_sum_ms=sum(v for name,v in stages.items() if name not in ('color','dct')),
                              all_kernel_sum_ms=sum(stages.values()),raw_h2d_bytes=frame['raw_upload']['bytes'],
                              raw_h2d_ms=(frame['raw_upload']['end']-frame['raw_upload']['start'])/1e6,
                              all_h2d_bytes=sum(copy['bytes'] for copy in ups),all_h2d_ms=sum(copy['end']-copy['start'] for copy in ups)/1e6,
                              all_d2h_bytes=sum(copy['bytes'] for copy in downs),all_d2h_ms=sum(copy['end']-copy['start'] for copy in downs)/1e6,
                              d2h_copies=[dict(bytes=copy['bytes'],duration_ms=(copy['end']-copy['start'])/1e6,correlationId=copy['correlationId']) for copy in downs],
                              raw_h2d_through_last_d2h_ms=frame['upload_through_last_download_ms']))
    warm=frame_rows[report['warmup']:]
    med=lambda key:statistics.median(row[key] for row in warm)
    stages={name:statistics.median(row['kernel_stages_ms'][name] for row in warm) for name in warm[0]['kernel_stages_ms']}
    summary=dict(case=report['case'],warm_frames=len(warm),warmup=report['warmup'],stage_median_ms=stages,
                 median_entropy_kernel_sum_ms=med('entropy_kernel_sum_ms'),median_all_kernel_sum_ms=med('all_kernel_sum_ms'),
                 transfer_medians={key:med(key) for key in ('raw_h2d_bytes','raw_h2d_ms','all_h2d_bytes','all_h2d_ms','all_d2h_bytes','all_d2h_ms','raw_h2d_through_last_d2h_ms')},
                 unprofiled_complete_host=report['unprofiled']['warm_primary'],rows=frame_rows)
    result.append(summary)
(w/'stage-summary.json').write_text(json.dumps(dict(unit='milliseconds',boundary='Kernel stage values and active DMA durations are independent profiled diagnostics; unprofiled host timing separately includes CPU/API gaps. Category medians must not be added to claim a median total.',cases=result),indent=2))
for item in result:
    print(item['case'],item['stage_median_ms'],item['transfer_medians'])
