"""Reprocess immutable raw QPC/log observations into continuous primary latency.

This does not execute codecs or modify raw stdout/TSV inputs. It retains the
previous two-interval-sum processed files separately for traceability.
"""
import argparse,json,pathlib,shutil,statistics
from benchmark import apply_qpc_observations,stats,digest
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('directory',type=pathlib.Path)
args=p.parse_args(); w=args.directory.resolve()
plan=json.loads((w/'plan.json').read_text()); rows=json.loads((w/'runs.json').read_text())
warmup=plan['warmup']; iterations=plan['iterations']
def resolve_qpc(path):
    original=pathlib.Path(path)
    candidates=[original] if original.is_absolute() else [w/original]
    candidates.append(w/'logs'/original.name)
    for candidate in candidates:
        if candidate.is_file(): return candidate.resolve()
    raise FileNotFoundError(f'QPC log missing: {path}; expected original path or {w / "logs" / original.name}')
raws=[resolve_qpc(r['qpc_log']) for r in rows if r['variant']=='fastvideo']
hashes_before={str(path):digest(path) for path in raws}
for name in ('plan.json','runs.json','summary.json'):
    backup=w/('two-interval-sum-'+name)
    if not backup.exists(): shutil.copy2(w/name,backup)
for row in rows:
    if row['variant']=='fastvideo':
        row['timing']=apply_qpc_observations(row['timing'],resolve_qpc(row['qpc_log']),iterations,warmup)
        row['primary_valid']=True
summaries=[]
for c in plan['inputs']:
    for variant in plan['binaries']:
        selected=[r for r in rows if r['case']==c['name'] and r['variant']==variant]
        all_primary=[x for r in selected for x in r['timing']['primary_ms'][warmup:]]
        item=dict(case=c['name'],variant=variant,primary_valid=all(r['primary_valid'] for r in selected),
                  primary_boundary='Continuous memory-input header-load START through completed host-pixel download STOP',
                  warm_primary=stats(all_primary),primary_round_medians_ms=[r['timing']['warm_primary']['median_ms'] for r in selected],
                  primary_median_round_ms=statistics.median(r['timing']['warm_primary']['median_ms'] for r in selected),
                  warm_transfer_inclusive=stats([x for r in selected for x in r['timing']['reported_transfer_inclusive_ms'][warmup:]]),
                  round_medians_ms=[r['timing']['warm_reported']['median_ms'] for r in selected],
                  median_round_ms=statistics.median(r['timing']['warm_reported']['median_ms'] for r in selected),
                  process_wall_per_iteration=stats([r['process_wall_per_iteration_ms'] for r in selected]),
                  output_pixel_hashes=sorted(set(r['output_image']['pixels_sha256'] for r in selected)))
        if variant=='fastvideo':
            item['warm_qpc_intervening_gap']=stats([x for r in selected for x in r['timing']['qpc_intervening_gap_ms'][warmup:]])
            item['invalid_printed_load_plus_codec_sum']=stats([x for r in selected for x in r['timing']['invalid_printed_load_plus_codec_ms'][warmup:]])
        summaries.append(item)
plan['boundary_notes']['primary']='Fastvideo CONTINUOUS header-load QPC START through completed-download decode QPC STOP, including the intervening host gap; GPUJPEG continuous Decode Image API timer. Cached one-time Fastvideo disk-load charge is excluded. All raw QPC TSV and stdout inputs are unchanged.'
plan['primary_postprocessing']='finalize_qpc.py; absolute raw counters preserve host gap that two-interval sum omitted.'
(w/'plan.json').write_text(json.dumps(plan,indent=2))
(w/'runs.json').write_text(json.dumps(rows,indent=2))
(w/'summary.json').write_text(json.dumps(summaries,indent=2))
if any(digest(pathlib.Path(path))!=h for path,h in hashes_before.items()): raise RuntimeError('Raw QPC input mutated during postprocessing')
(w/'qpc-raw-sha256.json').write_text(json.dumps(hashes_before,indent=2))
print('Reprocessed',len(rows),'runs; raw QPC TSV files unchanged.')
