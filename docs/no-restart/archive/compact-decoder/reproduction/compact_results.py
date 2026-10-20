"""Compact immutable benchmark results and index their raw-observation hashes.

This script performs no GPU execution and never changes stdout or QPC logs.
"""
import argparse,csv,json,pathlib
from benchmark import digest

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('directory',type=pathlib.Path)
args=p.parse_args(); directory=args.directory.resolve()
plan=json.loads((directory/'plan.json').read_text())
summary=json.loads((directory/'summary.json').read_text())
runs=json.loads((directory/'runs.json').read_text())
labels=list(plan['binaries']); rows=[]
for case in plan['inputs']:
    selected={row['variant']:row for row in summary if row['case']==case['name']}
    if set(selected)!=set(labels): raise ValueError('Incomplete case: '+case['name'])
    baseline=selected['baseline']['primary_median_round_ms']
    for variant in labels:
        observation=selected[variant]
        if not observation['primary_valid']: raise ValueError('Invalid primary metric')
        pixels=observation['output_pixel_hashes']; baseline_pixels=selected['baseline']['output_pixel_hashes']
        if len(pixels)!=1: raise ValueError(f'Non-deterministic pixel bytes: {case["name"]} {variant}')
        rows.append(dict(case=case['name'],variant=variant,
                         primary_median_round_ms=observation['primary_median_round_ms'],
                         primary_round_medians_ms=observation['primary_round_medians_ms'],
                         minimum_round_median_ms=min(observation['primary_round_medians_ms']),
                         maximum_round_median_ms=max(observation['primary_round_medians_ms']),
                         paired_round_variant_over_baseline=[v/b for v,b in zip(observation['primary_round_medians_ms'],selected['baseline']['primary_round_medians_ms'])],
                         baseline_over_variant=baseline/observation['primary_median_round_ms'],
                         variant_over_baseline=observation['primary_median_round_ms']/baseline,
                         pixel_sha256=pixels[0],exact_pixels_equal_baseline=pixels==baseline_pixels))
        if variant!='fastvideo' and pixels!=baseline_pixels:
            raise ValueError(f'GPUJPEG output mismatch: {case["name"]} {variant}')
(directory/'compact.json').write_text(json.dumps(rows,indent=2))
with (directory/'compact.csv').open('w',newline='') as output:
    writer=csv.DictWriter(output,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
logs=sorted((directory/'logs').glob('*'))
index=[dict(path='logs/'+path.name,bytes=path.stat().st_size,sha256=digest(path)) for path in logs if path.is_file()]
(directory/'raw-observation-sha256.json').write_text(json.dumps(index,indent=2))
print(f'Compacted {len(rows)} case/variant rows; hashed {len(index)} raw observations.')
