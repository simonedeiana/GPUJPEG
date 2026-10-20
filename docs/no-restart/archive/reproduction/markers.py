import pathlib,argparse
parser=argparse.ArgumentParser(description='Inventory JPEG header markers up to SOS')
parser.add_argument('files',nargs='+',type=pathlib.Path)
args=parser.parse_args()
for p in args.files:
 a=p.read_bytes(); i=2; print(p,len(a))
 while i<len(a):
  if a[i]!=255: i+=1; continue
  j=i+1
  while a[j]==255:j+=1
  marker=a[j]; i=j+1
  if marker in [0,0xd8,0xd9] or 0xd0<=marker<=0xd7: continue
  size=int.from_bytes(a[i:i+2],'big'); print(hex(marker),i-2,size,repr(a[i+2:i+min(size,30)])); i+=size
  if marker==0xda: break
