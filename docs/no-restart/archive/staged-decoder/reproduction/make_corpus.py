#!/usr/bin/env python3
"""Build the shared JPEG corpus from the user's original Fastvideo sample images."""
import argparse,hashlib,json,subprocess
from pathlib import Path


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def dimensions(path):
    tokens=[]
    with path.open('rb') as f:
        while len(tokens)<4:
            line=f.readline(4096)
            if not line:raise ValueError('Truncated PNM header: '+str(path))
            tokens.extend(line.split(b'#',1)[0].split())
    if tokens[0] not in (b'P5',b'P6') or tokens[3]!=b'255':
        raise ValueError('Expected 8-bit binary PGM/PPM: '+str(path))
    return int(tokens[1]),int(tokens[2])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--images',type=Path,required=True)
    p.add_argument('--encoder',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    cases=[('hd444-r10','wild.8.ppm','444',10),('hd420-r10','wild.8.ppm','420',10),
           ('hd422-r10','wild.8.ppm','422',10),('4k444-r10','wild.4k.8.ppm','444',10),
           ('4k420-r10','wild.4k.8.ppm','420',10),('hdgray-r10','wild.8.pgm',None,10),
           ('hd444-r1','wild.8.ppm','444',1),('hd444-r32','wild.8.ppm','444',32),
           ('hd444-r0','wild.8.ppm','444',0),('4k420-r0','wild.4k.8.ppm','420',0)]
    rows=[]
    for name,filename,sampling,restart in cases:
        source=(a.images/filename).resolve();target=a.output/(name+'.jpg')
        cmd=[str(a.encoder.resolve()),'-e','-q','90','-i','-r',str(restart)]
        if sampling:cmd+=['--subsampled='+sampling]
        run=subprocess.run(cmd+[str(source),str(target.resolve())],capture_output=True,timeout=120)
        (a.output/(name+'.encode.log')).write_bytes(run.stdout+run.stderr)
        if run.returncode:raise RuntimeError('Encoding failed: '+name)
        width,height=dimensions(source);channels=3 if sampling else 1
        rows.append(dict(name=name,path=target.name,source=filename,source_sha256=sha(source),
            sha256=sha(target),bytes=target.stat().st_size,width=width,height=height,
            channels=channels,sampling=sampling or 'gray',quality=90,restart_interval=restart,
            expected_output_bytes=width*height*channels))
        print(name,rows[-1]['sha256'],flush=True)
    (a.output/'manifest.json').write_text(json.dumps(rows,indent=2))


if __name__=='__main__':main()
