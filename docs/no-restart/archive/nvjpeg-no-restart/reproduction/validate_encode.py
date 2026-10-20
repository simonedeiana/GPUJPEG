"""True-RI0 and original/candidate byte, quantizer and public-pixel parity."""
from pathlib import Path
import argparse,hashlib,json,os,subprocess
from jpeg_checks import JPEG,quality

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def environment():
    env=os.environ.copy();cuda=env.get('CUDA_PATH')
    if cuda:env['PATH']=str(Path(cuda)/'bin/x64')+os.pathsep+str(Path(cuda)/'bin')+os.pathsep+env.get('PATH','')
    return env
def run_harness(binary,item,case,output,iterations=2,warmup=1):
    command=[str(binary.resolve()),'--input',item['raw'],'--width',str(item['width']),
        '--height',str(item['height']),'--channels',str(item['channels']),
        '--sampling',case['sampling'],'--quality',str(case['quality']),
        '--interleaved',str(case['interleaved']),'--optimized','0','--pinned','0',
        '--iterations',str(iterations),'--warmup',str(warmup),'--output',str(output.resolve())]
    result=subprocess.run(command,capture_output=True,timeout=120,env=environment())
    output.with_suffix('.stdout.log').write_bytes(result.stdout)
    output.with_suffix('.stderr.log').write_bytes(result.stderr)
    if result.returncode or not output.exists():raise RuntimeError(f'Encoder failed: {binary} {case["name"]}: {result.returncode}')
    try:return json.loads(result.stdout)
    except Exception:raise RuntimeError(f'Invalid harness JSON: {output}.stdout.log')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--reference',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--pillow-path',type=Path)
    p.add_argument('--identity',action='append',default=[],help='LABEL=PATH source/library identity')
    p.add_argument('--allow-container-difference',action='store_true')
    args=p.parse_args();root=args.output.resolve();root.mkdir(parents=True,exist_ok=True)
    manifest=json.loads(args.manifest.read_text());inputs={x['name']:x for x in manifest['inputs']}
    for item in inputs.values():
        if sha(item['raw'])!=item['raw_sha256']:raise RuntimeError('Raw input identity changed')
    identities={'reference':str(args.reference.resolve()),'candidate':str(args.candidate.resolve())}
    identities.update(dict(item.split('=',1) for item in args.identity))
    before={label:{'path':str(path),'sha256':sha(path)} for label,path in identities.items()}
    (root/'provenance-before.json').write_text(json.dumps(before,indent=2))
    results=[]
    for case in manifest['correctness']:
        item=inputs[case['input']];entry={'case':case,'input':item['name']};paths={};images={}
        for label,binary in [('reference',args.reference),('candidate',args.candidate)]:
            path=root/(case['name']+'-'+label+'.jpg');paths[label]=path
            timing=run_harness(binary,item,case,path)
            image=JPEG(path.read_bytes());images[label]=image
            audit=image.audit(require_ri0=True)
            if (audit['width'],audit['height'])!=(item['width'],item['height']):raise AssertionError('Wrong output dimensions')
            entry[label]={'audit':audit,'quality':quality(path,item,args.pillow_path),
                          'harness_iterations':timing['iterations']}
        before_bytes=paths['reference'].read_bytes();after_bytes=paths['candidate'].read_bytes()
        entry['byte_identical']=before_bytes==after_bytes
        entry['quantization_identical']=entry['reference']['audit']['component_quantization_tables']==entry['candidate']['audit']['component_quantization_tables']
        entry['public_pixels_identical']=entry['reference']['quality']['decoded_pixels_sha256']==entry['candidate']['quality']['decoded_pixels_sha256']
        if not entry['byte_identical']:
            entry['reference']['coefficients']=images['reference'].coefficients()
            entry['candidate']['coefficients']=images['candidate'].coefficients()
            entry['coefficients_identical']=entry['reference']['coefficients']['coefficient_sha256_by_component']==entry['candidate']['coefficients']['coefficient_sha256_by_component']
        else:entry['coefficients_identical']='implied by exact complete JPEG bytes'
        results.append(entry);(root/'results.json').write_text(json.dumps(results,indent=2))
        if not entry['quantization_identical'] or not entry['public_pixels_identical'] or entry['coefficients_identical'] is False:
            raise AssertionError(f'Semantic encoding mismatch: {case["name"]}')
        if not entry['byte_identical'] and not args.allow_container_difference:
            raise AssertionError(f'JPEG byte mismatch with semantic equality: {case["name"]}; diagnostics saved')
        print(case['name'],'PASS','bytes',len(after_bytes),'stuffed',entry['candidate']['audit']['stuffed_ff00_pairs'],flush=True)
    after={label:{'path':str(path),'sha256':sha(path)} for label,path in identities.items()}
    (root/'provenance-after.json').write_text(json.dumps(after,indent=2))
    if before!=after:raise RuntimeError('Source/library/harness identity changed')
    summary={'cases':len(results),'byte_identical':sum(x['byte_identical'] for x in results),
             'all_true_ri0':all(x['candidate']['audit']['true_ri0'] for x in results),
             'all_public_pixels_identical':all(x['public_pixels_identical'] for x in results),
             'candidate_ff00_pairs':sum(x['candidate']['audit']['stuffed_ff00_pairs'] for x in results),
             'instrumented_benchmark_claim':False}
    (root/'summary.json').write_text(json.dumps(summary,indent=2));print(summary)

if __name__=='__main__':main()
