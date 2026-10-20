"""Pair frozen GPUJPEG RI0 and nvJPEG stage diagnostics on identical raw inputs."""
import json,pathlib,sqlite3,statistics
w=pathlib.Path(__file__).resolve().parent
reference={x['case']:x for x in json.loads((w/'profiles/stage-summary.json').read_text())['cases']}
candidate=json.loads((w/'candidate-profiles/profiles.json').read_text());output=[]
for report in candidate:
    rows=[]
    for frame in report['frames']:
        packed=False;stages={name:0.0 for name in ('color','dct','block_entropy','bit_offset_scan','assemble_clear_pad','stuff_length_scan','stuff_write')}
        for k in frame['kernels']:
            name=k['name'];category=None
            if 'gpujpeg_preprocessor_raw_to_comp_kernel' in name:category='color'
            elif 'gpujpeg_dct_gpu_kernel' in name:category='dct'
            elif 'gpujpeg_r0_encode_blocks_warp' in name:category='block_entropy'
            elif 'gpujpeg_r0_clear_words' in name or 'gpujpeg_r0_pack_bits' in name or 'gpujpeg_r0_pad_scan' in name:
                category='assemble_clear_pad'
                if 'gpujpeg_r0_pack_bits' in name:packed=True
            elif 'gpujpeg_r0_stuff_bytes<(bool)0>' in name or 'gpujpeg_r0_allocate_scan' in name:category='stuff_length_scan'
            elif 'gpujpeg_r0_stuff_bytes<(bool)1>' in name:category='stuff_write'
            elif 'cub::' in name and 'DeviceScan' in name:category='stuff_length_scan' if packed else 'bit_offset_scan'
            if category is None:raise ValueError('Unclassified executed kernel '+name)
            stages[category]+=(k['end']-k['start'])/1e6
        rows.append(dict(iteration=frame['iteration'],stages_ms=stages,
                         entropy_kernel_sum_ms=sum(v for k,v in stages.items() if k not in ('color','dct')),
                         all_kernel_sum_ms=sum(stages.values()),raw_h2d_bytes=frame['raw_upload']['bytes'],
                         raw_h2d_ms=(frame['raw_upload']['end']-frame['raw_upload']['start'])/1e6,
                         all_h2d_bytes=sum(x['bytes'] for x in frame['uploads']),all_h2d_ms=sum(x['end']-x['start'] for x in frame['uploads'])/1e6,
                         all_d2h_bytes=sum(x['bytes'] for x in frame['downloads']),all_d2h_ms=sum(x['end']-x['start'] for x in frame['downloads'])/1e6,
                         raw_h2d_through_last_d2h_ms=frame['upload_through_last_download_ms']))
    warm=rows[report['warmup']:];ref=reference[report['case']];refwarm=ref['rows'][ref['warmup']:]
    med=lambda key:statistics.median(row[key] for row in warm)
    cand=dict(stage_median_ms={name:statistics.median(row['stages_ms'][name] for row in warm) for name in warm[0]['stages_ms']},
              entropy_kernel_sum_median_ms=med('entropy_kernel_sum_ms'),all_kernel_sum_median_ms=med('all_kernel_sum_ms'),
              transfer_medians={key:med(key) for key in ('raw_h2d_bytes','raw_h2d_ms','all_h2d_bytes','all_h2d_ms','all_d2h_bytes','all_d2h_ms','raw_h2d_through_last_d2h_ms')},rows=rows)
    nv=dict(stage_median_ms={name:ref['stage_median_ms'][name] for name in ref['stage_median_ms']},
            combined_ac_dc_median_ms=statistics.median(row['kernel_stages_ms']['ac']+row['kernel_stages_ms']['dc'] for row in refwarm),
            entropy_kernel_sum_median_ms=ref['median_entropy_kernel_sum_ms'],all_kernel_sum_median_ms=ref['median_all_kernel_sum_ms'],
            transfer_medians=ref['transfer_medians'])
    c=sqlite3.connect(w/'profiles'/(report['case']+'.sqlite'));c.row_factory=sqlite3.Row
    memset=[]
    original=json.loads((w/'profiles/profiles.json').read_text());old=next(x for x in original if x['case']==report['case'])
    for frame in old['frames'][10:]:
        end=max(x['end'] for x in frame['downloads'])
        ops=[dict(x) for x in c.execute('SELECT start,end,value,bytes FROM CUPTI_ACTIVITY_KIND_MEMSET WHERE start>=? AND end<=?',(frame['raw_upload']['start'],end))]
        memset.append(dict(iteration=frame['iteration'],bytes=sum(x['bytes'] for x in ops),active_ms=sum(x['end']-x['start'] for x in ops)/1e6,operations=ops))
    nv['memset']=dict(median_bytes=statistics.median(x['bytes'] for x in memset),median_active_ms=statistics.median(x['active_ms'] for x in memset),rows=memset)
    nv['entropy_kernels_plus_all_memsets_median_ms']=statistics.median(row['entropy_kernel_sum_ms']+zero['active_ms'] for row,zero in zip(refwarm,memset))
    nv['all_kernels_plus_all_memsets_median_ms']=statistics.median(row['all_kernel_sum_ms']+zero['active_ms'] for row,zero in zip(refwarm,memset))
    c2=sqlite3.connect(w/'candidate-profiles'/(report['case']+'.sqlite'));c2.row_factory=sqlite3.Row
    candidate_memset=[]
    for frame in report['frames'][10:]:
        end=max(x['end'] for x in frame['downloads'])
        ops=[dict(x) for x in c2.execute('SELECT start,end,value,bytes FROM CUPTI_ACTIVITY_KIND_MEMSET WHERE start>=? AND end<=?',(frame['raw_upload']['start'],end))]
        candidate_memset.append(dict(iteration=frame['iteration'],bytes=sum(x['bytes'] for x in ops),active_ms=sum(x['end']-x['start'] for x in ops)/1e6,operations=ops))
    cand['memset']=dict(median_bytes=statistics.median(x['bytes'] for x in candidate_memset),median_active_ms=statistics.median(x['active_ms'] for x in candidate_memset),rows=candidate_memset)
    cand['entropy_kernels_plus_all_memsets_median_ms']=statistics.median(row['entropy_kernel_sum_ms']+zero['active_ms'] for row,zero in zip(warm,candidate_memset))
    cand['all_kernels_plus_all_memsets_median_ms']=statistics.median(row['all_kernel_sum_ms']+zero['active_ms'] for row,zero in zip(warm,candidate_memset))
    output.append(dict(case=report['case'],raw_input_sha256=report['raw_input_sha256'],candidate_sha256=report['candidate_sha256'],warm_frames=len(warm),candidate=cand,nvjpeg=nv))
(w/'paired-stage-comparison.json').write_text(json.dumps(dict(unit='milliseconds',scope='Independent profiled stage observations; primary balanced host latency is measured separately. Vendor AC/DC are separate launches whereas candidate integrates them. Candidate assembly category includes clear/pad kernels; vendor memset activities are reported separately. Never sum category medians to estimate a median total.',cases=output),indent=2))
for case in output:
    print(case['case'],'candidate',case['candidate']['stage_median_ms'],'entropy',case['candidate']['entropy_kernel_sum_median_ms'],'nvjpeg entropy',case['nvjpeg']['entropy_kernel_sum_median_ms'])
