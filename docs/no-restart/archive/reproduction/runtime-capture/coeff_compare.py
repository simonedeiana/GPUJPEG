"""Independent baseline Huffman parser for quantized-coefficient comparison.

This deliberately reads only sequential Huffman JPEG, not progressive scans.
It validates exact values before and after the captured host transformation.
"""
from pathlib import Path
from collections import Counter
import hashlib, struct, json, argparse
w=Path(__file__).resolve().parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source',type=Path,default=w.parent/'no-restart.jpg')
parser.add_argument('--payload',type=Path,default=w/'no-rst/h2d-014-694474.bin')
parser.add_argument('--restart',type=int,default=16)
parser.add_argument('--output-dir',type=Path,default=w)
args=parser.parse_args()
args.output_dir.mkdir(parents=True,exist_ok=True)
src=args.source.read_bytes()

def headers(b):
    p=2; huff={}; components={}; dri=0
    while True:
        assert b[p]==255
        marker=b[p+1]; size=int.from_bytes(b[p+2:p+4],'big'); payload=b[p+4:p+2+size]
        if marker==0xc0:
            precision,height,width,n=struct.unpack('>BHHB',payload[:6]); assert precision==8
            for i in range(n):
                cid,hv,tq=payload[6+3*i:9+3*i]; components[cid]=(hv>>4,hv&15,tq)
        if marker==0xc4:
            q=0
            while q<len(payload):
                tc=payload[q]; counts=payload[q+1:q+17]; vals=payload[q+17:q+17+sum(counts)]; q+=17+sum(counts)
                table={}; code=0; j=0
                for bits,count in enumerate(counts,1):
                    for _ in range(count): table[(bits,code)]=vals[j]; code+=1; j+=1
                    code*=2
                huff[tc]=table
        if marker==0xdd: dri=int.from_bytes(payload,'big')
        if marker==0xda:
            n=payload[0]; scan=[(payload[1+2*i],payload[2+2*i]>>4,payload[2+2*i]&15) for i in range(n)]
            assert payload[-3:]==bytes([0,63,0])
            return dict(components=components,huff=huff,dri=dri,scan=scan,offset=p+2+size,width=width,height=height)
        p+=size+2

class Bits:
    def __init__(self,b): self.b=b; self.p=0
    def read(self,n):
        value=0
        for _ in range(n): value=(value<<1)|((self.b[self.p>>3]>>(7-(self.p&7)))&1); self.p+=1
        return value
    def symbol(self,table):
        code=0
        for n in range(1,17):
            code=(code<<1)|self.read(1)
            if (n,code) in table: return table[n,code]
        raise ValueError('invalid Huffman code')
    def value(self,n):
        v=self.read(n)
        return v if n==0 or v>=1<<(n-1) else v-((1<<n)-1)

def segments(b):
    parts=[]; current=bytearray(); p=0
    while p<len(b)-1:
        val=b[p]; p+=1
        if val==255:
            m=b[p]; p+=1
            if m==0: current.append(255); continue
            assert 0xd0<=m<=0xd7 or m==0xd9,hex(m)
            parts.append(bytes(current)); current.clear()
            if m==0xd9: break
        else: current.append(val)
    return parts

def decode(b,h,dri):
    parts=segments(b); c=h['components']; mx=max(v[0] for v in c.values()); my=max(v[1] for v in c.values())
    total=((h['width']+8*mx-1)//(8*mx))*((h['height']+8*my-1)//(8*my))
    all_blocks=[]; consumed=[]; hashes=[]
    mcu=0
    for part in parts:
        bit=Bits(part); dc={k:0 for k in c}; num=min(dri or total,total-mcu)
        for j in range(num):
            for cid,td,ta in h['scan']:
                for k in range(c[cid][0]*c[cid][1]):
                    block=[0]*64; dc[cid]+=bit.value(bit.symbol(h['huff'][td])); block[0]=dc[cid]
                    i=1
                    while i<64:
                        rs=bit.symbol(h['huff'][16+ta]); run,n=rs>>4,rs&15
                        if n==0:
                            if run==0: break
                            assert run==15; i+=16; continue
                        i+=run; assert i<64
                        block[i]=bit.value(n); i+=1
                    all_blocks.append(block)
        consumed.append(bit.p); mcu+=num
    assert mcu==total
    raw=b''.join(struct.pack('<64h',*b) for b in all_blocks)
    return all_blocks,dict(mcus=total,blocks=len(all_blocks),segments=len(parts),coefficients_sha256=hashlib.sha256(raw).hexdigest(),first20_consumed_bits=consumed[:20],total_consumed_bits=sum(consumed))

h=headers(src); captured=args.payload.read_bytes()
a,sa=decode(src[h['offset']:],h,0)
b,sb=decode(captured,h,args.restart)
diff=[(i,k,x,y) for i,(aa,bb) in enumerate(zip(a,b)) for k,(x,y) in enumerate(zip(aa,bb)) if x!=y]
result=dict(original=sa,**{'captured_as_dri'+str(args.restart):sb},difference_count=len(diff),first20_differences=diff[:20],
            original_entropy_offset=h['offset'],original_entropy_size=len(src)-h['offset'],captured_size=len(captured))
(args.output_dir/'coefficient-comparison.json').write_text(json.dumps(result,indent=2)); print(json.dumps(result,indent=2))
# A separate valid JPEG carrying the exact captured entropy stream with DRI16.
# Useful to verify whether this is a lossless coefficient-domain transcode.
sos=src.rfind(b'\xff\xda',0,h['offset'])
(args.output_dir/('repacked-dri'+str(args.restart)+'.jpg')).write_bytes(src[:sos]+b'\xff\xdd\x00\x04'+args.restart.to_bytes(2,'big')+src[sos:h['offset']]+captured)
