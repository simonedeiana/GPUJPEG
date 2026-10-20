#!/usr/bin/env python3
"""Independent staged decoder parity through quantized DCT and public pixels.

Requirements: Python 3, Pillow >= 10.2 with JPEG restart support, CUDA GPU,
two decoder_dump executables linked against original and staged GPUJPEG.
Run with --help. Generated files are restricted to the selected output folder.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import random
import subprocess
import sys

def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--reference-tool', type=Path, required=True,
                        help='Original gpujpegtool for planar JPEG generation')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--corpus-manifest', type=Path)
    parser.add_argument('--pillow-path', type=Path)
    parser.add_argument('--skip-dual', action='store_true')
    parser.add_argument('--reuse-reference', action='store_true',
                        help='Reuse prior reference dumps for exactly the regenerated inputs')
    parser.add_argument('--candidate-label', default='candidate',
                        help='Separate output folder/result label, e.g. const')
    args = parser.parse_args()
    if not args.candidate_label.replace('-','').replace('_','').isalnum():
        parser.error('--candidate-label must contain only letters, digits, hyphens or underscores')
    if args.pillow_path:
        sys.path.insert(0, str(args.pillow_path))
    from PIL import Image, ImageFile
    ImageFile.MAXBLOCK = 8 * 1024 * 1024
    root = args.output.resolve()
    results_path = root/('results.json' if args.candidate_label=='candidate' else f'results-{args.candidate_label}.json')
    previous_inputs = None
    if args.reuse_reference:
        previous_manifest = json.loads((root/'manifest.json').read_text())
        previous_inputs = [(entry['name'], entry['sha256']) for entry in previous_manifest]
        if previous_inputs != [(entry['name'], digest(entry['path'])) for entry in previous_manifest]:
            raise RuntimeError('Existing input identities differ from reference manifest; rerun without --reuse-reference')
    fixtures = root/'fixtures'
    fixtures.mkdir(parents=True, exist_ok=True)
    cases = []
    if args.corpus_manifest:
        for entry in json.loads(args.corpus_manifest.read_text()):
            image_path = Path(entry['path'])
            if not image_path.is_absolute():
                image_path = args.corpus_manifest.parent/image_path
            cases.append({'name':entry['name'], 'path':str(image_path.resolve()), 'kind':'natural'})
    rng = random.Random(6731)
    # Structured but textured RGB input keeps nontrivial DC variation.
    width, height = 641, 479
    image = Image.frombytes('RGB', (width,height), bytes(
        (x*17 + y*23 + c*43 + rng.randrange(32)) % 256
        for y in range(height) for x in range(width) for c in range(3)))
    def save(name, im, **options):
        path = fixtures/(name+'.jpg')
        im.save(path, 'JPEG', **options)
        cases.append({'name':name,'path':str(path),'kind':'synthetic','options':options})
    for sub, code in [('444',0),('422',1),('420',2)]:
        for optimize in [False,True]:
            save(f'odd-{sub}-opt{int(optimize)}', image, quality=91,
                 subsampling=code, optimize=optimize, restart_marker_blocks=7)
    for count in [31,32,33]:
        gray = Image.frombytes('L',(8,count*8),bytes((x*13+y*19)%256 for y in range(count*8) for x in range(8)))
        save(f'threshold-gray-{count}',gray,quality=95,restart_marker_blocks=1)
    # 6 blocks/MCU and RI=33 spans many 32-block predictor tiles per segment.
    save('dc-tile-carry-420',image,quality=96,subsampling=2,restart_marker_blocks=33)
    save('dc-tile-carry-444',image,quality=96,subsampling=0,restart_marker_blocks=33)
    dense = Image.frombytes('RGB',(256,256),rng.randbytes(256*256*3))
    save('dense64tokens',dense,quality=100,subsampling=0,restart_marker_blocks=1)
    save('dense-opt-custom-dht',dense,quality=83,subsampling=0,optimize=True,restart_marker_blocks=3)
    save('sparse-eob',Image.new('RGB',(641,479),(127,123,129)),quality=1,
         subsampling=0,restart_marker_blocks=3)
    save('no-restart-optimized',image,quality=87,optimize=True,subsampling=0)
    ppm = fixtures/'odd.ppm'
    image.save(ppm)
    for sub in ['444','422','420']:
        path = fixtures/f'planar-{sub}.jpg'
        run = subprocess.run([str(args.reference_tool.resolve()),'-e','-q','91','-r','3',
                              f'--subsampled={sub}',str(ppm),str(path)],capture_output=True,timeout=60)
        (fixtures/f'planar-{sub}-encode.log').write_bytes(run.stdout+run.stderr)
        if run.returncode or not path.exists(): raise RuntimeError(f'Planar generation failed: {sub}')
        cases.append({'name':f'planar-{sub}','path':str(path),'kind':'synthetic-planar'})
    for case in cases:
        case['sha256'] = digest(case['path'])
    if previous_inputs is not None and previous_inputs != [(case['name'],case['sha256']) for case in cases]:
        raise RuntimeError('Regenerated input identities differ; rerun without --reuse-reference')
    (root/'manifest.json').write_text(json.dumps(cases,indent=2))

    results=[]
    # Fresh instances also cover CPU fallback. Upstream CPU coefficient-buffer
    # capacity tracking is not safe for arbitrary GPU->CPU resize sequences;
    # exercise staged lifetime changes using GPU-eligible images only below.
    for index, case in enumerate(cases):
        pair=[]
        for variant,binary in [('reference',args.reference),(args.candidate_label,args.candidate)]:
            folder=root/f'fresh-{variant}'/f'{index:03d}'
            folder.mkdir(parents=True,exist_ok=True)
            if variant!='reference' or not args.reuse_reference:
                process=subprocess.run([str(binary.resolve()),str(folder),'single',case['path']],capture_output=True,timeout=90)
                (folder/'decode.log').write_bytes(process.stdout+process.stderr)
                if process.returncode: raise RuntimeError(f'fresh/{variant}/{case["name"]}: return {process.returncode}; see log')
            pair.append(folder/'w0-000')
        row={'mode':'fresh','case':case['name'],'source_index':index}
        metas=[json.loads(path.with_suffix('.json').read_text()) for path in pair]
        if metas[0]!=metas[1]: raise AssertionError(f'fresh metadata mismatch {case["name"]}')
        row.update(metas[0])
        for suffix in ['pixels','coefficients']:
            hashes=[digest(path.with_suffix('.'+suffix)) for path in pair]
            if hashes[0]!=hashes[1]: raise AssertionError(f'fresh {suffix} mismatch {case["name"]}')
            row[suffix+'_sha256']=hashes[0]
        row['gpu_eligible']=row['segments']>=32
        results.append(row)
        print('fresh',case['name'],'PASS','segments',row['segments'],flush=True)
        results_path.write_text(json.dumps(results,indent=2))
    eligible=[case for case,row in zip(cases,results) if row['gpu_eligible']]
    for mode in ['single'] if args.skip_dual else ['single','dual']:
        for variant,binary in [('reference',args.reference),(args.candidate_label,args.candidate)]:
            folder=root/f'{mode}-{variant}'
            folder.mkdir(exist_ok=True)
            if variant!='reference' or not args.reuse_reference:
                command=[str(binary.resolve()),str(folder),mode,*[case['path'] for case in eligible]]
                process=subprocess.run(command,capture_output=True,timeout=300)
                (root/f'{mode}-{variant}.log').write_bytes(process.stdout+process.stderr)
                if process.returncode: raise RuntimeError(f'{mode}/{variant}: return {process.returncode}; see log')
        for worker in range(1 if mode=='single' else 2):
            for index,case in enumerate(eligible):
                base=f'w{worker}-{index:03d}'
                reference=root/f'{mode}-reference'/base
                candidate=root/f'{mode}-{args.candidate_label}'/base
                refmeta=json.loads(reference.with_suffix('.json').read_text())
                candmeta=json.loads(candidate.with_suffix('.json').read_text())
                if refmeta != candmeta: raise AssertionError(f'Metadata mismatch {base}: {refmeta} != {candmeta}')
                row={'mode':mode,'worker':worker,'case':case['name'],**refmeta}
                for suffix in ['pixels','coefficients']:
                    before=digest(reference.with_suffix('.'+suffix))
                    after=digest(candidate.with_suffix('.'+suffix))
                    row[suffix+'_sha256']=before
                    if before != after: raise AssertionError(f'{suffix} mismatch {mode}/{base} {case["name"]}')
                row['gpu_eligible']=refmeta['segments']>=32
                results.append(row)
                print(mode,worker,case['name'],'PASS','segments',refmeta['segments'],flush=True)
        results_path.write_text(json.dumps(results,indent=2))
    print(f'{len(results)} coefficient and public pixel comparisons passed across {len(cases)} inputs.')

if __name__=='__main__':
    main()
