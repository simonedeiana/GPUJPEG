"""Generate the ten natural JPEG comparison inputs using the original encoder."""
from pathlib import Path
import argparse,hashlib,json,subprocess

def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def dimensions(path):
    with open(path,'rb') as stream:
        def token():
            result=b''
            while not result:
                value=stream.read(1)
                if not value:raise ValueError('Incomplete PNM header')
                if value==b'#':stream.readline();continue
                if value.isspace():continue
                result=value
            while True:
                value=stream.read(1)
                if not value or value.isspace():return result
                result+=value
        magic=token();width=int(token());height=int(token());maximum=int(token())
    if magic not in [b'P5',b'P6'] or maximum!=255:raise ValueError('Expected 8-bit binary PGM/PPM')
    return width,height,1 if magic==b'P5' else 3

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--encoder',type=Path,required=True)
    p.add_argument('--hd-rgb',type=Path,required=True)
    p.add_argument('--large-rgb',type=Path,required=True)
    p.add_argument('--gray',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();root=args.output.resolve();root.mkdir(parents=True,exist_ok=True)
    hd=args.hd_rgb.resolve();large=args.large_rgb.resolve();gray=args.gray.resolve()
    cases=[('hd444-r10',hd,'444',10),('hd420-r10',hd,'420',10),
           ('hd422-r10',hd,'422',10),('4k444-r10',large,'444',10),
           ('4k420-r10',large,'420',10),('hdgray-r10',gray,None,10),
           ('hd444-r1',hd,'444',1),('hd444-r32',hd,'444',32),
           ('hd444-r0',hd,'444',0),('4k420-r0',large,'420',0)]
    result=[]
    for name,source,sampling,restart in cases:
        output=root/(name+'.jpg')
        command=[str(args.encoder.resolve()),'-e','-q','90','-i','-r',str(restart)]
        if sampling:command+=['--subsampled='+sampling]
        command+=[str(source),str(output)]
        run=subprocess.run(command,capture_output=True,timeout=120)
        (root/(name+'.encode.log')).write_bytes(run.stdout+run.stderr)
        if run.returncode or not output.exists():raise RuntimeError(f'Encoding failed: {name}')
        width,height,channels=dimensions(source)
        result.append({'name':name,'path':output.name,'sha256':sha(output),'source':source.name,
                       'source_sha256':sha(source),'width':width,'height':height,'channels':channels,
                       'sampling':sampling or 'gray','quality':90,'restart_interval':restart})
        print(name,output.stat().st_size,flush=True)
    (root/'manifest.json').write_text(json.dumps(result,indent=2))

if __name__=='__main__':main()
