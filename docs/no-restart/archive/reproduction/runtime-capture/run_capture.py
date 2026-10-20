"""Capture only an explicitly spawned fast_jpeg process through CUDA injection.

Run from the task root after build.cmd. Uses the existing CUDA CUPTI library.
Do not use timings from this capture as performance results: file writes run
inside API entry callbacks and deliberately perturb the process.
"""
import argparse, os, pathlib, subprocess
w=pathlib.Path(__file__).resolve().parent
parser=argparse.ArgumentParser()
parser.add_argument('--input',required=True,type=pathlib.Path)
parser.add_argument('--output',required=True,type=pathlib.Path)
parser.add_argument('--exe',required=True,type=pathlib.Path,help='User-supplied original fast_jpeg.exe')
parser.add_argument('--helper',default=w/'capture.dll',type=pathlib.Path)
parser.add_argument('--cupti-bin',type=pathlib.Path,default=pathlib.Path(os.environ.get('CUDA_PATH',r'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.4'))/'extras/CUPTI/lib/x64')
args=parser.parse_args()
dest=args.output.resolve(); dest.mkdir(parents=True,exist_ok=True)
exe=args.exe.resolve()
env=os.environ.copy()
env['CUDA_INJECTION64_PATH']=str(args.helper.resolve())
env['FASTJPEG_CAPTURE_DIR']=str(dest)
env['PATH']=str(args.cupti_bin.resolve())+';'+env['PATH']
with (dest/'stdout.txt').open('wb') as log:
    subprocess.run([str(exe),'-i',str(args.input.resolve()),'-o',str(dest/'result.ppm'),'-repeat','1','-info'],env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
