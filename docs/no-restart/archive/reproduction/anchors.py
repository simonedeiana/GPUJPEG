import pathlib,re,argparse
parser=argparse.ArgumentParser(description='Print selected instruction anchors from extract.py output')
parser.add_argument('directory',type=pathlib.Path)
args=parser.parse_args()
for n,patterns in {'HuffmanDecodeKernel':['mov.u32 %r130','mul.lo.s32 %r2','ld.global.u32 %r307','mul.wide.u32 %rd43','ld.global.u8 %r353','bfi.b32 %r208','st.global.u32 [%rd66]','atom.global.or'], 'RleDecodeKernel':['mul.wide.u32 %rd3','bfe.u32 %r54','st.global.u16 [%rd44]','bfe.u32 %r82','selp.b32 %r176','st.global.u16 [%rd45]'], 'DCSumKernel':['mul.lo.s32 %r2','ld.global.s16','st.global.u16'], 'DCRestoreKernel':['div.u32','ld.global.s16','sub.s16','st.global.u16'], 'BytestreamUnstuffKernel':['shl.b32 %r188','shfl.sync.up','st.global.u32','st.global.u8']}.items():
 lines=(args.directory/(n+'.ptx')).read_text().splitlines(); print(n)
 for pattern in patterns:
  hits=[(i+1,x) for i,x in enumerate(lines) if pattern in x]; print(pattern,hits[:5])
