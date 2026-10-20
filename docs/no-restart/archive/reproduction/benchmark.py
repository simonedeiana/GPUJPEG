#!/usr/bin/env python3
"""Alternate codec binaries and retain warm GPUJPEG verbose timing samples."""
import argparse, json, re, statistics, subprocess, tempfile
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('baseline', type=Path)
    p.add_argument('candidate', type=Path)
    p.add_argument('image', type=Path)
    p.add_argument('--codec', choices=['encode', 'decode'], required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--rounds', type=int, default=3)
    p.add_argument('--iterations', type=int, default=110)
    p.add_argument('--discard', type=int, default=10)
    a, flags = p.parse_known_args()
    if flags[:1] == ['--']: flags = flags[1:]
    if a.rounds < 1 or not 0 <= a.discard < a.iterations:
        p.error('require rounds>=1 and 0<=discard<iterations')
    prefix = 'Encode' if a.codec == 'encode' else 'Decode'
    stage = 'Encoder' if a.codec == 'encode' else 'Decoder'
    patterns = {
        'codec_ms': rf'{prefix} Image:\s+([\d.]+) ms',
        'gpu_ms': rf'{prefix} Image GPU:\s+([\d.]+) ms',
        'huffman_ms': rf'Huffman {stage}:\s+([\d.]+) ms',
    }
    result = dict(codec=a.codec, image=str(a.image.resolve()), flags=flags,
                  iterations=a.iterations, discard=a.discard, runs=[])
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='gpujpeg-benchmark-') as temp:
        for repeat in range(a.rounds):
            order = ['baseline', 'candidate'] if repeat % 2 == 0 else ['candidate', 'baseline']
            for kind in order:
                binary = getattr(a, kind).resolve()
                dest = Path(temp)/('result.jpg' if a.codec == 'encode' else 'result.ppm')
                cmd = [str(binary), '-e' if a.codec == 'encode' else '-d', '-vv',
                       '-n', str(a.iterations), *flags, str(a.image.resolve()), str(dest)]
                proc = subprocess.run(cmd, capture_output=True, timeout=300)
                text = (proc.stdout+proc.stderr).decode(errors='replace')
                if proc.returncode: raise RuntimeError(text)
                run = dict(kind=kind, repeat=repeat, command=cmd)
                for metric, pattern in patterns.items():
                    values = list(map(float, re.findall(pattern, text)))[a.discard:]
                    if len(values) != a.iterations-a.discard:
                        raise RuntimeError(f'{metric}: unexpected sample count {len(values)}')
                    run[metric] = dict(median=statistics.median(values), samples=values)
                result['runs'].append(run)
                a.output.write_text(json.dumps(result, indent=2))
                print(kind, repeat, {m: run[m]['median'] for m in patterns}, flush=True)
    result['summary'] = {
        kind: {m: statistics.median(r[m]['median'] for r in result['runs'] if r['kind']==kind)
               for m in patterns} for kind in ['baseline', 'candidate']}
    a.output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result['summary'], indent=2))


if __name__ == '__main__': main()
