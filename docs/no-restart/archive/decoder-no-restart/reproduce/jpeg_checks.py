"""Independent JPEG marker/quantizer audit, sequential coefficients and quality.

The marker parser scans entropy framing, so FF D0 bytes inside APP/COM payloads
cannot be mistaken for restart markers. Coefficient parsing supports sequential
8-bit Huffman scans, including separate component scans and restart intervals.
"""
from pathlib import Path
import argparse,hashlib,json,math,struct,sys

class Bits:
    def __init__(self,data):self.data=data;self.pos=0;self.buffer=0;self.count=0;self.consumed=0
    def fill(self,n):
        while self.count<n:
            if self.pos>=len(self.data):raise ValueError('Truncated entropy code')
            self.buffer=(self.buffer<<8)|self.data[self.pos];self.pos+=1;self.count+=8
    def read(self,n):
        self.fill(n);self.count-=n;self.consumed+=n
        value=(self.buffer>>self.count)&((1<<n)-1)
        self.buffer&=(1<<self.count)-1
        return value
    def symbol(self,table):
        if self.count>=8 or self.pos<len(self.data):
            self.fill(8);entry=table['quick'][(self.buffer>>(self.count-8))&255]
            if entry is not None:
                length,symbol=entry;self.read(length);return symbol,length
        code=0
        for length in range(1,17):
            code=(code<<1)|self.read(1)
            if (length,code) in table['codes']:return table['codes'][length,code],length
        raise ValueError('Unused Huffman prefix')
    def value(self,n):
        value=self.read(n)
        return value if not n or value>=(1<<(n-1)) else value+1-(1<<n)

class JPEG:
    def __init__(self,data):
        self.data=data;self.quant={};self.huffman={};self.frame=None;self.scans=[];self.dri=[];self.markers=[]
        if data[:2]!=b'\xff\xd8':raise ValueError('Missing JPEG SOI')
        pos=2;restart=0;ended=False
        while pos<len(data):
            start=pos
            if data[pos]!=255:raise ValueError(f'Missing marker at {pos}')
            while pos<len(data) and data[pos]==255:pos+=1
            if pos>=len(data):raise ValueError('Truncated marker')
            kind=data[pos];pos+=1
            self.markers.append({'marker':hex(kind),'offset':start})
            if kind==0xd9:ended=True;break
            if kind==0x01:continue
            if kind==0xd8 or 0xd0<=kind<=0xd7:raise ValueError('Unexpected standalone marker')
            if pos+2>len(data):raise ValueError('Missing marker length')
            length=int.from_bytes(data[pos:pos+2],'big')
            if length<2 or pos+length>len(data):raise ValueError('Invalid marker length')
            payload=data[pos+2:pos+length];pos+=length
            if kind==0xdb:
                i=0
                while i<len(payload):
                    ident=payload[i];i+=1;precision=ident>>4;table=ident&15
                    if precision not in [0,1]:raise ValueError('Bad quantizer precision')
                    size=64*(precision+1);raw=payload[i:i+size];i+=size
                    if len(raw)!=size:raise ValueError('Truncated quantizer')
                    self.quant[table]=list(raw) if not precision else list(struct.unpack('>64H',raw))
            elif kind==0xc4:
                i=0
                while i<len(payload):
                    ident=payload[i];counts=payload[i+1:i+17];i+=17
                    if len(counts)!=16:raise ValueError('Truncated DHT counts')
                    values=payload[i:i+sum(counts)];i+=sum(counts)
                    if len(values)!=sum(counts):raise ValueError('Truncated DHT symbols')
                    codes={};quick=[None]*256;code=0;symbol_index=0
                    for bits,count in enumerate(counts,1):
                        if code+count>1<<bits:raise ValueError('Oversubscribed Huffman tree')
                        for _ in range(count):
                            value=values[symbol_index];symbol_index+=1
                            codes[bits,code]=value
                            if bits<=8:
                                for suffix in range(1<<(8-bits)):quick[(code<<(8-bits))+suffix]=(bits,value)
                            code+=1
                        code<<=1
                    self.huffman[ident]={'codes':codes,'quick':quick}
            elif kind in [0xc0,0xc1,0xc2]:
                precision,height,width,components=struct.unpack('>BHHB',payload[:6])
                items={}
                if len(payload)!=6+3*components:raise ValueError('Invalid SOF component list')
                for index in range(components):
                    ident,hv,quantizer=payload[6+3*index:9+3*index]
                    items[ident]={'h':hv>>4,'v':hv&15,'quantizer':quantizer}
                self.frame={'marker':kind,'precision':precision,'width':width,'height':height,'components':items}
            elif kind==0xdd:
                if len(payload)!=2:raise ValueError('Invalid DRI')
                restart=int.from_bytes(payload,'big');self.dri.append(restart)
            elif kind==0xda:
                if not self.frame:raise ValueError('SOS before SOF')
                n=payload[0]
                if len(payload)!=1+2*n+3:raise ValueError('Invalid SOS')
                components=[{'id':payload[1+2*i],'dc':payload[2+2*i]>>4,'ac':payload[2+2*i]&15} for i in range(n)]
                parts=[];rst=[];stuffed=0;encoded_bytes=0;begin=pos
                while True:
                    ff=data.find(b'\xff',pos)
                    if ff<0:raise ValueError('Entropy has no end marker')
                    next_pos=ff+1
                    while next_pos<len(data) and data[next_pos]==255:next_pos+=1
                    if next_pos>=len(data):raise ValueError('Truncated entropy marker')
                    value=data[next_pos]
                    if value==0:
                        if next_pos!=ff+1:raise ValueError('Invalid repeated FF stuffing')
                        stuffed+=1;pos=next_pos+1;continue
                    raw=data[begin:ff];encoded_bytes+=len(raw)
                    parts.append(raw.replace(b'\xff\x00',b'\xff'))
                    if 0xd0<=value<=0xd7:
                        rst.append(value);pos=next_pos+1;begin=pos;continue
                    pos=ff;break
                self.scans.append({'components':components,'spectral':list(payload[-3:]),'dri':restart,
                    'restart_markers':rst,'stuffed_pairs':stuffed,'entropy_bytes':encoded_bytes,
                    'parts':parts,'tables':self.huffman.copy()})
        if not ended:raise ValueError('Missing EOI')
        if not self.frame or not self.scans:raise ValueError('Missing frame or scans')
        self.trailing_bytes=len(data)-pos

    def audit(self,require_ri0=False):
        frame=self.frame
        result={'bytes':len(self.data),'sha256':hashlib.sha256(self.data).hexdigest(),
            'sof_marker':hex(frame['marker']),'precision':frame['precision'],
            'width':frame['width'],'height':frame['height'],'components':frame['components'],
            'scan_count':len(self.scans),'dri_values':self.dri,
            'rst_count':sum(len(scan['restart_markers']) for scan in self.scans),
            'stuffed_ff00_pairs':sum(scan['stuffed_pairs'] for scan in self.scans),
            'entropy_bytes':sum(scan['entropy_bytes'] for scan in self.scans),
            'trailing_bytes':self.trailing_bytes,'quantization_tables':self.quant,
            'component_quantization_tables':[self.quant[value['quantizer']] for value in frame['components'].values()],
            'scan_components':[[value['id'] for value in scan['components']] for scan in self.scans]}
        result['true_ri0']=not result['rst_count'] and all(value==0 for value in self.dri)
        if require_ri0 and (not result['true_ri0'] or self.trailing_bytes):raise ValueError('Output is not a complete true-RI0 JPEG')
        return result

    def coefficients(self,strict_padding=True):
        frame=self.frame
        if frame['marker']!=0xc0 or frame['precision']!=8:raise ValueError('Only baseline 8-bit sequential coefficients supported')
        components=frame['components'];hmax=max(x['h'] for x in components.values());vmax=max(x['v'] for x in components.values())
        hashes={ident:hashlib.sha256() for ident in components};counts={ident:0 for ident in components}
        scanned=[];max_delta=0;long_codes=0;zrl=0;padding=[];block_offsets_mod32=[0]*32;max_block_bits=0
        for scan in self.scans:
            if scan['spectral']!=[0,63,0]:raise ValueError('Nonsequential SOS')
            selected=scan['components'];single=len(selected)==1
            if single:
                factor=components[selected[0]['id']]
                mx=(frame['width']*factor['h']+8*hmax-1)//(8*hmax)
                my=(frame['height']*factor['v']+8*vmax-1)//(8*vmax)
            else:
                mx=(frame['width']+8*hmax-1)//(8*hmax);my=(frame['height']+8*vmax-1)//(8*vmax)
            total=mx*my;done=0;restart=scan['dri']
            expected_parts=(total+restart-1)//restart if restart else 1
            if len(scan['parts'])!=expected_parts:
                raise ValueError('Wrong entropy segment count')
            for part_index,part in enumerate(scan['parts']):
                bits=Bits(part);dc={ident:0 for ident in components}
                mcus=min(restart or total,total-done)
                for mcu in range(mcus):
                    for item in selected:
                        ident=item['id'];factor=components[ident]
                        for block_index in range(1 if single else factor['h']*factor['v']):
                            block_start=bits.consumed;block_offsets_mod32[block_start%32]+=1
                            block=[0]*64
                            category,length=bits.symbol(scan['tables'][item['dc']]);long_codes+=length==16
                            difference=bits.value(category);max_delta=max(max_delta,abs(difference));dc[ident]+=difference;block[0]=dc[ident]
                            coefficient=1
                            while coefficient<64:
                                symbol,length=bits.symbol(scan['tables'][16+item['ac']]);long_codes+=length==16
                                run,category=symbol>>4,symbol&15
                                if not category:
                                    if not run:break
                                    if run!=15:raise ValueError('Invalid zero-length AC symbol')
                                    coefficient+=16;zrl+=1
                                    if coefficient>64:raise ValueError('ZRL exceeds block')
                                    continue
                                coefficient+=run
                                if coefficient>=64:raise ValueError('AC run exceeds block')
                                block[coefficient]=bits.value(category);coefficient+=1
                            hashes[ident].update(struct.pack('<64h',*block));counts[ident]+=1
                            max_block_bits=max(max_block_bits,bits.consumed-block_start)
                done+=mcus
                remainder=len(part)*8-bits.consumed
                legal=0<=remainder<=7 and (not remainder or part[-1]&((1<<remainder)-1)==(1<<remainder)-1)
                padding.append({'bits':remainder,'all_one_padding':legal})
                if strict_padding and not legal:raise ValueError(f'Illegal entropy padding: {padding[-1]}')
            scanned.extend(item['id'] for item in selected)
        if sorted(scanned)!=sorted(components):raise ValueError('Components missing or multiply scanned')
        return {'coefficient_sha256_by_component':{ident:value.hexdigest() for ident,value in hashes.items()},
                'blocks_by_component':counts,'max_abs_dc_difference':max_delta,
                'sixteen_bit_symbols':long_codes,'zrl_symbols':zrl,'padding':padding,
                'block_start_bit_offsets_mod32_histogram':block_offsets_mod32,'max_block_bits':max_block_bits}

def quality(jpeg_path,input_item,pillow_path=None):
    if pillow_path:sys.path.insert(0,str(pillow_path))
    from PIL import Image,ImageChops,ImageStat
    mode='L' if input_item['channels']==1 else 'RGB'
    expected=Image.frombytes(mode,(input_item['width'],input_item['height']),Path(input_item['raw']).read_bytes())
    with Image.open(jpeg_path) as image:decoded=image.convert(mode)
    if decoded.size!=expected.size:raise ValueError('Decoded dimensions differ')
    difference=ImageChops.difference(expected,decoded);statistics=ImageStat.Stat(difference)
    mse=sum(statistics.sum2)/(expected.width*expected.height*input_item['channels'])
    return {'decoded_pixels_sha256':hashlib.sha256(decoded.tobytes()).hexdigest(),
            'mse_to_input':mse,'psnr_db_to_input':None if mse==0 else 10*math.log10(255*255/mse),
            'lossless_pixels':mse==0,'max_abs_pixel_error':max(value[1] for value in statistics.extrema)}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('jpeg',type=Path);p.add_argument('--ri0',action='store_true');p.add_argument('--coefficients',action='store_true')
    args=p.parse_args();image=JPEG(args.jpeg.read_bytes());result=image.audit(args.ri0)
    if args.coefficients:result['coefficient_check']=image.coefficients()
    print(json.dumps(result,indent=2))
