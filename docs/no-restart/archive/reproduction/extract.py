import pathlib,re,json,argparse
parser=argparse.ArgumentParser(description='Extract normalized RGB8 decoder PTX and generic DC kernels from cuobjdump output')
parser.add_argument('ptx_dump',type=pathlib.Path)
parser.add_argument('directory',type=pathlib.Path)
args=parser.parse_args()
args.directory.mkdir(parents=True,exist_ok=True)
p=args.ptx_dump; s=p.read_text(); matches=list(re.finditer(r'(?m)^\.visible \.entry (\S+)\(',s)); seen=set(); index=[]
for i,m in enumerate(matches):
 name=m[1]
 base=next((x for x in ['HuffmanDecodeKernel','RleDecodeKernel','BytestreamUnstuffKernel','BytestreamMarkupMarkerAndFFRestKernel','BytestreamMarkupMarkerAndFFKernel','BytestreamUnstuffRestKernel','DCSumKernel','DCRestoreKernel'] if x in name),None)
 if not base or base in seen or ('SurfaceTrait' in name and 'Format_t5E' not in name): continue
 text=s[m.start():(matches[i+1].start() if i+1<len(matches) else len(s))]; text=text[:text.find('\n}')+2]; text='\n'.join(x for x in text.splitlines() if x.strip()); (args.directory/(base+'.ptx')).write_text(text); seen.add(base); index.append(dict(name=name,line=s.count('\n',0,m.start())+1,file=base+'.ptx'))
(args.directory/'index.json').write_text(json.dumps(index,indent=2)); print(json.dumps(index,indent=2))
