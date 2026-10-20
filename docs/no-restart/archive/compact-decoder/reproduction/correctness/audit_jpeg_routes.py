"""Audit actual fixture JPEG headers, DHT identity and entropy route conditions."""
from pathlib import Path
import hashlib,json,sys

def audit(path):
    data=Path(path).read_bytes()
    pos=2; scans=[]; dht=[]; restart=None; frame=None
    while pos<len(data):
        if data[pos]!=255: raise ValueError(f'Expected marker at {pos}')
        while data[pos]==255: pos+=1
        marker=data[pos];pos+=1
        if marker==217: break
        length=int.from_bytes(data[pos:pos+2],'big')
        payload=data[pos+2:pos+length];pos+=length
        if marker==196: dht.append(payload)
        if marker==221: restart=int.from_bytes(payload,'big')
        if marker==192:
            frame={'precision':payload[0],'height':int.from_bytes(payload[1:3],'big'),
                   'width':int.from_bytes(payload[3:5],'big'),'components':payload[5],
                   'sampling':[list(payload[6+i*3:9+i*3]) for i in range(payload[5])]}
        if marker==218:
            mappings=[{'component':payload[1+2*i],'dc':payload[2+2*i]>>4,'ac':payload[2+2*i]&15}
                      for i in range(payload[0])]
            rst=0
            while pos<len(data):
                if data[pos]!=255:pos+=1;continue
                offset=pos
                while data[pos]==255:pos+=1
                next_marker=data[pos];pos+=1
                if next_marker==0:continue
                if 208<=next_marker<=215:rst+=1;continue
                pos=offset;break
            scans.append({'mappings':mappings,'restart_markers':rst})
    return {'frame':frame,'dri':restart,'scans':scans,
            'dht_sha256':hashlib.sha256(b''.join(dht)).hexdigest(),
            'supported_gpu_tables':all(m['dc']==m['ac'] and m['dc']<=1 for s in scans for m in s['mappings'])}

if __name__=='__main__':
    manifest=Path(sys.argv[1])
    results=[{'name':case['name'],'sha256':case['sha256'],**audit(case['path'])}
             for case in json.loads(manifest.read_text())]
    output=manifest.with_name('jpeg-dht-audit.json')
    output.write_text(json.dumps(results,indent=2))
    print(len(results),'headers audited;',len({x['dht_sha256'] for x in results}),
          'distinct DHT identities;',sum(x['supported_gpu_tables'] for x in results),'with GPU-supported table mappings')
