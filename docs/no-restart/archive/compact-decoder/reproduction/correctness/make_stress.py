"""Generate valid baseline grayscale JPEGs with explicit rare entropy cases.

No GPU or image package is required. Optional --pillow-path also verifies that
libjpeg can decode every generated fixture. Quantized coefficients are known
independently of both tested GPUJPEG decoder implementations.
"""
from pathlib import Path
import argparse, hashlib, json, random, struct, sys

ZIGZAG=[0,1,8,16,9,2,3,10,17,24,32,25,18,11,4,5,
        12,19,26,33,40,48,41,34,27,20,13,6,7,14,21,28,
        35,42,49,56,57,50,43,36,29,22,15,23,30,37,44,51,
        58,59,52,45,38,31,39,46,53,60,61,54,47,55,62,63]

def table(lengths):
    bits=[0]*16
    symbols=[]
    for length,symbol in sorted(lengths):
        bits[length-1]+=1
        symbols.append(symbol)
    codes={};code=0;index=0
    for length,count in enumerate(bits,1):
        for _ in range(count):
            symbol=symbols[index];index+=1
            assert code < (1<<length)-1, 'JPEG forbids an all-one Huffman code'
            codes[symbol]=f'{code:0{length}b}'
            code+=1
        code<<=1
    return bytes(bits+symbols),codes

def amplitude(value):
    category=abs(value).bit_length()
    if not category:return 0,''
    encoded=value if value>=0 else (1<<category)-1+value
    return category,f'{encoded:0{category}b}'

def marker(kind,payload):
    return b'\xff'+bytes([kind])+struct.pack('>H',len(payload)+2)+payload

def make(name,root,restart,holes=False):
    if holes:
        dc_spec=[(1,0),(16,10),(16,11)]
        ac_spec=[(1,0),(16,10),(16,0x41)]
    else:
        dc_spec=[(i+1,i) for i in range(11)]+[(16,11)]
        ac_symbols=[0,1,2,3,4,5,6,7,8,9,10,0x11,0x21,0x31,0xf0,0x41]
        ac_spec=[(i+1,symbol) for i,symbol in enumerate(ac_symbols)]
    dc_bytes,dc=table(dc_spec);ac_bytes,ac=table(ac_spec)
    # Forty full segments plus a one-block final segment. Exactly 41 segments
    # use the GPU entropy route despite the deliberately small pixel area.
    blocks=40*restart+1
    encoded=bytearray(b'\xff\xd8')
    encoded+=marker(0xdb,bytes([0])+bytes([1])*64)
    encoded+=marker(0xc0,bytes([8])+struct.pack('>HH',blocks*8,8)+bytes([1,1,0x11,0]))
    encoded+=marker(0xc4,bytes([0])+dc_bytes+bytes([0x10])+ac_bytes)
    encoded+=marker(0xdd,struct.pack('>H',restart))
    encoded+=marker(0xda,bytes([1,1,0,0,63,0]))
    expected=bytearray();segments=[];long_codes=0
    rng=random.Random(9341+restart+holes)
    for segment_start in range(0,blocks,restart):
        bits=[];predictor=0
        for block in range(segment_start,min(segment_start+restart,blocks)):
            ordinal=block-segment_start
            coefficients=[0]*64
            # Include genuinely one-byte segments, long-code DC differences,
            # full 64-token blocks and sparse high-run long-code symbols.
            mode=(block*7+segment_start//restart)%5
            target=0 if mode==0 else (1023 if ordinal%2==0 else -1023)
            coefficients[0]=target
            category,value=amplitude(target-predictor);predictor=target
            assert category in dc,(category,name)
            bits.extend([dc[category],value]);long_codes+=len(dc[category])==16
            if mode==0:
                bits.append(ac[0])
            elif mode in [1,2]:
                # Densest baseline AC magnitude, with plentiful FF/00 pairs.
                for zigzag in range(1,64):
                    coefficients[ZIGZAG[zigzag]]=1023
                    bits.extend([ac[10],'1'*10]);long_codes+=len(ac[10])==16
            elif mode==3 or holes:
                coefficients[ZIGZAG[5]]=1
                bits.extend([ac[0x41],'1',ac[0]]);long_codes+=len(ac[0x41])==16
            else:
                # 16 zero AC coefficients before the nonzero at position 17.
                coefficients[ZIGZAG[17]]=1
                bits.extend([ac[0xf0],ac[1],'1',ac[0]])
            expected+=struct.pack('<64h',*coefficients)
        bitstring=''.join(bits)
        bitstring+='1'*((-len(bitstring))%8)
        raw=bytes(int(bitstring[i:i+8],2) for i in range(0,len(bitstring),8))
        stuffed=raw.replace(b'\xff',b'\xff\x00')
        starts=[i for i in range(len(stuffed)-1) if stuffed[i:i+2]==b'\xff\x00']
        segments.append({'compressed_bytes':len(stuffed),'stuffing_pairs':len(starts),
                         'ff_offsets':starts,'final_segment':segment_start+restart>=blocks})
        encoded+=stuffed
        if segment_start+restart<blocks:
            encoded+=b'\xff'+bytes([0xd0+(segment_start//restart)%8])
    encoded+=b'\xff\xd9'
    path=root/(name+'.jpg');path.write_bytes(encoded)
    coefficients=root/(name+'.expected-coefficients');coefficients.write_bytes(expected)
    crosses={str(size):sum(sum(offset%size==size-1 for offset in seg['ff_offsets']) for seg in segments)
             for size in [16,32,64,128,256,512]}
    return {'name':name,'path':str(path.resolve()),'sha256':hashlib.sha256(encoded).hexdigest(),
            'kind':'known-coefficients','expected_coefficients':str(coefficients.resolve()),
            'expected_sha256':hashlib.sha256(expected).hexdigest(),
            'restart_interval':restart,'segments':len(segments),'blocks':blocks,
            'long16bit_symbol_count':long_codes,'large_code_holes':holes,
            'ff00_crossings':crosses,'minimum_segment_bytes':min(x['compressed_bytes'] for x in segments),
            'maximum_segment_bytes':max(x['compressed_bytes'] for x in segments),
            'segment_details':segments}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--pillow-path',type=Path)
    args=p.parse_args();root=args.output.resolve();root.mkdir(parents=True,exist_ok=True)
    cases=[]
    for holes in [False,True]:
        for restart in [1,2,3,7,17]:
            cases.append(make(f'{"holes16" if holes else "chain16"}-r{restart}',root,restart,holes))
    if args.pillow_path:
        sys.path.insert(0,str(args.pillow_path));from PIL import Image
        for case in cases:
            with Image.open(case['path']) as image:image.load()
            case['pillow_libjpeg_decode']='passed'
    (root/'manifest.json').write_text(json.dumps(cases,indent=2))
    print(len(cases),'valid baseline JPEG fixtures; long16-code occurrences',sum(x['long16bit_symbol_count'] for x in cases))
    print('FF00 crossing totals',{str(n):sum(x['ff00_crossings'][str(n)] for x in cases) for n in [16,32,64,128,256,512]})
    print('Segment byte min/max',min(x['minimum_segment_bytes'] for x in cases),max(x['maximum_segment_bytes'] for x in cases))

if __name__=='__main__':main()
