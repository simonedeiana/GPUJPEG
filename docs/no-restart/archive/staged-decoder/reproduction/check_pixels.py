"""Validate decoder output identity and cross-codec numerical differences."""
from pathlib import Path
import argparse,json,hashlib,math,sys


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--images',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--pillow-path',type=Path)
    a=p.parse_args()
    if a.pillow_path:sys.path.insert(0,str(a.pillow_path))
    from PIL import Image
    cases=json.loads(a.manifest.read_text()); results=[]
    for case in cases:
        suffix='.pgm' if case['channels']==1 else '.ppm'
        buffers={}
        for label in ['baseline','staged','fastvideo']:
            file=a.images/(case['name']+'-'+label+suffix)
            with Image.open(file) as im:
                if im.size!=(case['width'],case['height']):raise ValueError(str(file))
                expected='L' if case['channels']==1 else 'RGB'
                if im.mode!=expected:raise ValueError(str(file)+' '+im.mode)
                buffers[label]=im.tobytes()
        reference=buffers['baseline']; candidate=buffers['staged']
        if reference!=candidate:raise AssertionError('Staged output mismatch: '+case['name'])
        # Streaming integer aggregates avoid a large temporary float image.
        abs_sum=0; squared=0; largest=0; different=0
        for x,y in zip(reference,buffers['fastvideo']):
            d=abs(x-y);abs_sum+=d;squared+=d*d;largest=max(largest,d);different+=d!=0
        mse=squared/len(reference)
        results.append(dict(case=case['name'],staged_pixels_identical=True,
            pixel_bytes=len(reference),pixel_sha256={k:hashlib.sha256(v).hexdigest() for k,v in buffers.items()},
            fastvideo_vs_gpujpeg=dict(max_abs_error=largest,mean_abs_error=abs_sum/len(reference),
                rmse=math.sqrt(mse),psnr_db=10*math.log10(255*255/mse) if mse else None,
                differing_samples=different)))
        print(case['name'],'staged exact; Fastvideo max error',largest,flush=True)
    a.output.write_text(json.dumps(results,indent=2))


if __name__=='__main__':main()
