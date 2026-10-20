"""CPU-only independent entropy/padding audit of small synthetic validation JPEGs."""
from pathlib import Path
import argparse,json
from jpeg_checks import JPEG
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('validation_root',type=Path)
    args=p.parse_args();results=[];ff_mod256=[0]*256;ff_last_bytes=0
    for path in sorted(args.validation_root.glob('*-candidate.jpg')):
        if path.name.startswith(('hd-','4k-')):continue
        image=JPEG(path.read_bytes());decoded=image.coefficients()
        for scan in image.scans:
            for part in scan['parts']:
                ff_last_bytes+=int(part.endswith(b'\xff'))
                position=part.find(b'\xff')
                while position>=0:
                    ff_mod256[position%256]+=1;position=part.find(b'\xff',position+1)
        results.append({'file':path.name,**decoded})
    block_offsets=[sum(x['block_start_bit_offsets_mod32_histogram'][i] for x in results) for i in range(32)]
    summary={'files':len(results),'max_abs_dc_difference':max(x['max_abs_dc_difference'] for x in results),
        'sixteen_bit_symbols':sum(x['sixteen_bit_symbols'] for x in results),'zrl_symbols':sum(x['zrl_symbols'] for x in results),
        'all_legal_one_padding':all(p['all_one_padding'] for x in results for p in x['padding']),
        'padding_bit_counts':sorted({p['bits'] for x in results for p in x['padding']}),
        'raw_ff_offsets_mod256_histogram':ff_mod256,'raw_ff_offsets_mod256_covered':sum(x>0 for x in ff_mod256),
        'raw_ff_last_scan_byte_cases':ff_last_bytes,'block_start_bit_offsets_mod32_histogram':block_offsets,
        'block_start_bit_offsets_mod32_covered':sum(x>0 for x in block_offsets),'max_block_bits':max(x['max_block_bits'] for x in results)}
    (args.validation_root/'entropy-diagnostics.json').write_text(json.dumps({'summary':summary,'files':results},indent=2))
    print(summary)
if __name__=='__main__':main()
