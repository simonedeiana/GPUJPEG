"""Prepare identical packed-host inputs and the RI0 test/benchmark matrices."""
from pathlib import Path
import argparse,hashlib,json,random,sys

def sha(data):return hashlib.sha256(data).hexdigest()

def pnm(path):
    data=Path(path).read_bytes();pos=0
    def token():
        nonlocal pos
        while True:
            while pos<len(data) and data[pos] in b' \t\r\n':pos+=1
            if pos<len(data) and data[pos]==35:
                pos=data.index(b'\n',pos)+1;continue
            break
        start=pos
        while pos<len(data) and data[pos] not in b' \t\r\n':pos+=1
        result=data[start:pos];delimiter=data[pos];pos+=1
        if delimiter==13 and pos<len(data) and data[pos]==10:pos+=1
        return result
    magic=token();width=int(token());height=int(token());maximum=int(token())
    if magic not in [b'P5',b'P6'] or maximum!=255:raise ValueError(f'Expected binary 8-bit PGM/PPM: {path}')
    channels=1 if magic==b'P5' else 3
    raw=data[pos:]
    if len(raw)!=width*height*channels:raise ValueError(f'Unexpected raw byte count: {path}: {len(raw)}')
    return width,height,channels,raw

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--hd-rgb',type=Path,required=True);p.add_argument('--large-rgb',type=Path,required=True)
    p.add_argument('--gray',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--pillow-path',type=Path)
    args=p.parse_args();root=args.output.resolve();root.mkdir(parents=True,exist_ok=True)
    if args.pillow_path:sys.path.insert(0,str(args.pillow_path))
    from PIL import Image
    inputs=[]
    def emit(name,width,height,channels,raw,source=None,derived=None):
        raw_path=root/(name+'.raw');raw_path.write_bytes(raw)
        if source:
            pnm_path=source.resolve();source_sha=sha(source.read_bytes())
        else:
            pnm_path=root/(name+'.pnm')
            pnm_path.write_bytes(f'P{5 if channels==1 else 6}\n{width} {height}\n255\n'.encode()+raw)
            source_sha=sha(pnm_path.read_bytes())
        item={'name':name,'raw':str(raw_path),'pnm':str(pnm_path),'width':width,'height':height,
              'channels':channels,'input_bytes':len(raw),'raw_sha256':sha(raw),'pnm_sha256':source_sha}
        if derived:item['derived']=derived
        inputs.append(item)
        return item
    for name,path in [('hd-rgb',args.hd_rgb),('4k-rgb',args.large_rgb),('hd-gray',args.gray)]:
        emit(name,*pnm(path),source=path)
    large=next(x for x in inputs if x['name']=='4k-rgb')
    gray=Image.frombytes('RGB',(large['width'],large['height']),Path(large['raw']).read_bytes()).convert('L').tobytes()
    emit('4k-gray',large['width'],large['height'],1,gray,
         derived='Pillow RGB.convert(L), prepared once outside every encoder timer from 4k-rgb packed bytes')
    rng=random.Random(192017)
    for name,width,height,channels,kind in [
        ('tiny-white',1,1,3,'white'),('tiny-black-gray',1,1,1,'black'),
        ('odd-rgb',17,19,3,'gradient'),('checker-rgb',257,129,3,'checker'),
        ('noise-rgb',127,65,3,'noise'),('flat-rgb',64,64,3,'flat'),
        ('noise-gray',129,65,1,'noise'),('checker-gray',257,129,1,'checker')]:
        raw=bytes(255 if kind=='white' else 0 if kind=='black' else 127 if kind=='flat'
                  else rng.randrange(256) if kind=='noise'
                  else 255*((x//8+y//8)%2) if kind=='checker'
                  else (x*17+y*29+c*47)%256
                  for y in range(height) for x in range(width) for c in range(channels))
        emit(name,width,height,channels,raw,derived=f'deterministic synthetic {kind}')
    byname={item['name']:item for item in inputs}
    def case(name,quality,sampling,interleaved=1):
        return {'name':f'{name}-q{quality}-{sampling}-'+('interleaved' if interleaved else 'planar'),
                'input':name,'quality':quality,'sampling':sampling,'interleaved':interleaved,
                'restart_interval':0,'optimized_huffman':0,'pinned':0}
    benchmark=[]
    for name in ['hd-rgb','4k-rgb','hd-gray','4k-gray']:
        for sampling in ['444','422','420'] if byname[name]['channels']==3 else ['gray']:
            benchmark.append(case(name,90,sampling))
    correctness=list(benchmark)
    for item in inputs[4:]:
        for quality in [1,75,90,100]:
            for sampling in ['444','420'] if item['channels']==3 else ['gray']:
                correctness.append(case(item['name'],quality,sampling))
    for quality in [1,75,90,100]:correctness.append(case('odd-rgb',quality,'422'))
    correctness.append(case('checker-rgb',100,'422'))
    for name,sampling in [('noise-rgb','444'),('noise-gray','gray'),('checker-gray','gray')]:
        correctness.append(case(name,0,sampling))
    for name in ['odd-rgb','checker-rgb','noise-rgb']:
        for sampling in ['444','422','420']:
            for quality in [75,100]:correctness.append(case(name,quality,sampling,0))
    manifest={'inputs':inputs,'benchmark_q90':benchmark,'correctness':correctness,
              'primary_timing_contract':'Pageable packed host raw input -> H2D -> encode -> complete compressed host JPEG -> stream synchronize. File IO/context/setup excluded; no stage event instrumentation.',
              'benchmark_defaults':{'rounds':5,'iterations':110,'discard_first':10,'quality':90}}
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print(len(inputs),'inputs;',len(benchmark),'Q90 benchmark cases;',len(correctness),'RI0 correctness cases')

if __name__=='__main__':main()
