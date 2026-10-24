"""Exercise real CLI parsing and RGB24 IO; requires a CUDA GPU, no Python packages.

python windows_cli.py --tool path/to/gpujpegtool.exe --out NEW_OUTPUT_DIRECTORY
Outputs and logs are retained for inspection. Also works with a POSIX CLI build.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess


def audit(path):
    """Parse markers and entropy framing, excluding APP payloads from RST counts."""
    data = path.read_bytes()
    assert data[:2] == b'\xff\xd8'
    pos, rst, dri, tables, dimensions = 2, 0, [], [], None
    while pos < len(data):
        assert data[pos] == 255
        while data[pos] == 255:
            pos += 1
        marker = data[pos]
        pos += 1
        if marker == 0xd9:
            assert pos == len(data)
            return dict(dimensions=dimensions, restart_intervals=dri,
                        restart_markers=rst,
                        quantizers_sha256=hashlib.sha256(b''.join(tables)).hexdigest())
        length = int.from_bytes(data[pos:pos + 2], 'big')
        payload = data[pos + 2:pos + length]
        assert length >= 2 and pos + length <= len(data)
        pos += length
        if marker == 0xc0:
            precision, height, width = struct.unpack('>BHH', payload[:5])
            assert precision == 8
            dimensions = [width, height]
        elif marker == 0xdd:
            dri.append(int.from_bytes(payload, 'big'))
        elif marker == 0xdb:
            tables.append(payload)
        elif marker == 0xda:
            while pos < len(data):
                if data[pos] != 255:
                    pos += 1
                    continue
                start = pos
                while data[pos] == 255:
                    pos += 1
                value = data[pos]
                pos += 1
                if value == 0:
                    continue
                if 0xd0 <= value <= 0xd7:
                    rst += 1
                    continue
                pos = start
                break
    raise AssertionError('Missing EOI')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tool', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    tool, output = args.tool.resolve(), args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    unicode_dir = output / 'RGB spazio caf\u00e8 \u6d4b\u8bd5'
    unicode_dir.mkdir()
    width, height = 37, 19
    raw = unicode_dir / 'input colori.rgb'
    raw.write_bytes(bytes((x * 17 + y * 31 + channel * 83) % 256
                          for y in range(height) for x in range(width) for channel in range(3)))
    records = []

    def run(label, options):
        command = [str(tool), *map(str, options)]
        result = subprocess.run(command, capture_output=True, timeout=60)
        (output / (label + '.stdout.log')).write_bytes(result.stdout)
        (output / (label + '.stderr.log')).write_bytes(result.stderr)
        assert result.returncode == 0, (label, result.returncode, result.stderr.decode(errors='replace'))
        return command

    cases = [
        ('long-equals', ['--encode', '--size=37x19', '--quality=95', '--restart=0',
                         '--colorspace=rgb', '--pixel-format=444-u8-p012',
                         '--subsampled=4:4:4', '--interleaved']),
        ('long-separated', ['--encode', '--size', '37x19', '--quality', '95', '--restart', '0',
                            '--colorspace', 'rgb', '--pixel-format', '444-u8-p012',
                            '--subsampled=4:4:4', '--interleaved=true']),
        ('short', ['-e', '-s', '37x19', '-q', '95', '-r', '0', '-c', 'rgb',
                   '-f', '444-u8-p012', '-S4:4:4', '-i']),
        ('restart-eight', ['-e', '-s', '37x19', '-q', '95', '-r', '8', '-c', 'rgb',
                           '-f', '444-u8-p012', '-S4:4:4', '-i']),
        ('quality-seventy', ['-e', '-s', '37x19', '-q', '70', '-r', '0', '-c', 'rgb',
                             '-f', '444-u8-p012', '-S4:4:4', '-i']),
    ]
    for label, options in cases:
        jpeg = unicode_dir / (label + '.jpg')
        encoded_command = run(label + '-encode', [*options, raw, jpeg])
        info = audit(jpeg)
        assert info['dimensions'] == [width, height]
        if label == 'restart-eight':
            assert info['restart_intervals'] == [8] and info['restart_markers'] > 0
        else:
            assert info['restart_markers'] == 0 and all(v == 0 for v in info['restart_intervals'])
        rgb = unicode_dir / (label + '.rgb')
        decoded_command = run(label + '-decode', ['--decode', '--colorspace=rgb',
                              '--pixel-format=444-u8-p012', jpeg, rgb])
        assert rgb.stat().st_size == width * height * 3
        records.append(dict(case=label, jpeg=info, encode_command=encoded_command,
                            decode_command=decoded_command, output_rgb_bytes=rgb.stat().st_size,
                            jpeg_sha256=hashlib.sha256(jpeg.read_bytes()).hexdigest()))
    assert len({r['jpeg_sha256'] for r in records[:3]}) == 1, 'Equivalent option forms differ'
    assert records[0]['jpeg']['quantizers_sha256'] != records[4]['jpeg']['quantizers_sha256'], 'Quality option ignored'
    first, second = unicode_dir / 'batch-first.jpg', unicode_dir / 'batch-second.jpg'
    run('batch-encode', [*cases[0][1], raw, first, raw, second])
    assert first.read_bytes() == second.read_bytes() == (unicode_dir / 'long-equals.jpg').read_bytes()
    (output / 'results.json').write_text(json.dumps(dict(passed=True, cases=records,
        batch_encode_passed=True, unicode_and_space_paths_passed=True), indent=2), encoding='utf-8')
    print('PASS: dimensions, RGB24 IO, short/long options, quality, RI0/RI8, Unicode paths and batch encoding')


if __name__ == '__main__':
    main()
