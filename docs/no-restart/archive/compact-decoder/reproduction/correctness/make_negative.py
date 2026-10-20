"""Framed but invalid entropy inputs for bounded-access sanitizer checks.

These are NOT valid-JPEG coefficient/parity fixtures. Empty segments exercise
the zero-size unstuff branch; all-one entropy exercises an unused canonical
prefix. Decoding may reject these inputs. Only sanitizer boundedness is tested.
"""
from pathlib import Path
import hashlib,json

root=Path(__file__).resolve().parent
data=(root/'stress/chain16-r1.jpg').read_bytes()
pos=2
while True:
    assert data[pos]==255
    marker=data[pos+1]
    size=int.from_bytes(data[pos+2:pos+4],'big')
    pos+=size+2
    if marker==218:break
prefix=data[:pos]
folder=root/'negative';folder.mkdir(exist_ok=True)
cases=[]
for name,entropy,purpose in [
    ('empty-segments',b'','zero-byte segments and guarded word-reader padding'),
    ('unused-prefix',b'\xff\x00\xff\x00','canonical prefix 0xffff beyond the final upper bound'),
]:
    output=bytearray(prefix)
    for index in range(41):
        output+=entropy
        if index<40:output+=bytes([255,208+index%8])
    output+=b'\xff\xd9'
    path=folder/(name+'.jpg');path.write_bytes(output)
    cases.append({'name':name,'path':str(path.resolve()),'sha256':hashlib.sha256(output).hexdigest(),
                  'classification':'invalid entropy; sanitizer bounded-access diagnostic only',
                  'purpose':purpose,'segments':41,'valid_jpeg_parity_claim':False})
(folder/'manifest.json').write_text(json.dumps(cases,indent=2))
print('Wrote',len(cases),'negative diagnostics; excluded from valid coefficient/pixel comparison counts.')
