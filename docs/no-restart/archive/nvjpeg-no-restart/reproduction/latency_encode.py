"""Separate small-image latency diagnostic; excludes the primary natural-image matrix."""
from pathlib import Path
import argparse,json,random,statistics
from validate_encode import run_harness,sha
from jpeg_checks import JPEG

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--baseline',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--prepare-only',action='store_true')
    args=p.parse_args();root=args.output.resolve();root.mkdir(parents=True,exist_ok=True)
    original={x['name']:x for x in json.loads(args.manifest.read_text())['inputs']}
    inputs=[dict(original['tiny-black-gray']),dict(original['noise-rgb'])]
    rng=random.Random(20261020)
    for name,width,height,channels in [('gray32',32,32,1),('noise512',512,512,3)]:
        raw=root/(name+'.raw')
        data=rng.randbytes(width*height*channels)
        if raw.exists() and raw.read_bytes()!=data:raise RuntimeError('Latency input identity changed')
        if not raw.exists():raw.write_bytes(data)
        inputs.append({'name':name,'width':width,'height':height,'channels':channels,'raw':str(raw),'raw_sha256':sha(raw)})
    (root/'inputs.json').write_text(json.dumps(inputs,indent=2))
    if args.prepare_only:return
    binaries={'baseline':args.baseline,'candidate':args.candidate};before={k:sha(v) for k,v in binaries.items()}
    results=[]
    for round_index in range(3):
        order=inputs[:];rng.shuffle(order)
        for item in order:
            case={'name':item['name'],'quality':90,'interleaved':1,'sampling':'gray' if item['channels']==1 else '420'}
            encoders=list(binaries);rng.shuffle(encoders)
            for label in encoders:
                output=root/f'r{round_index}-{item["name"]}-{label}.jpg'
                timing=run_harness(binaries[label],item,case,output,iterations=31,warmup=1)
                audit=JPEG(output.read_bytes()).audit(require_ri0=True)
                results.append({'round':round_index,'case':case,'encoder':label,'timing':timing,'audit':audit})
                (root/'results.json').write_text(json.dumps(results,indent=2))
                print(round_index,item['name'],label,timing['warm_primary']['median_ms'],'ms',flush=True)
    summaries=[]
    for item in inputs:
        by_label={label:[x for x in results if x['case']['name']==item['name'] and x['encoder']==label] for label in binaries}
        digests={x['audit']['sha256'] for runs in by_label.values() for x in runs}
        if len(digests)!=1:raise AssertionError('Small-image output changed')
        medians={label:[x['timing']['warm_primary']['median_ms'] for x in runs] for label,runs in by_label.items()}
        summary={'case':item['name'],'width':item['width'],'height':item['height'],'channels':item['channels'],
            'sampling':by_label['candidate'][0]['case']['sampling'],'round_medians_ms':medians,
            'median_of_round_medians_ms':{label:statistics.median(values) for label,values in medians.items()},'byte_identical':True,'true_ri0':True}
        summary['speedup_original_over_candidate']=summary['median_of_round_medians_ms']['baseline']/summary['median_of_round_medians_ms']['candidate']
        summaries.append(summary)
    after={k:sha(v) for k,v in binaries.items()}
    if before!=after:raise RuntimeError('Latency binaries changed')
    (root/'provenance.json').write_text(json.dumps({'before':before,'after':after},indent=2))
    (root/'summary.json').write_text(json.dumps(summaries,indent=2));print('DONE 24 small-image processes')
if __name__=='__main__':main()
