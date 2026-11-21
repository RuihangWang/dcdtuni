from pathlib import Path
import os,subprocess,json,time
P=Path(__file__).resolve().parent
A=P.parents[1];N=A/"src/backend/aircooling/third_parties/dcCartMesh";D=N/"src/mesh/dcCartMesh/dcCartCutCells"
W=Path("/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni");F=W/"include/OpenFOAM-2412/src"
env=dict(os.environ,CUDA_VISIBLE_DEVICES="",OMP_NUM_THREADS="1",OPENBLAS_NUM_THREADS="1",TMPDIR=str(P/"tmp"),WM_PROJECT_DIR=str(W),FOAM_ETC=str(W/"etc"))
common=["taskset","-c","8-15","g++","-std=c++17","-m64","-pthread","-DOPENFOAM=2412","-DWM_DP","-DWM_LABEL_SIZE=32","-DNoRepository","-ftemplate-depth-100","-fPIC","-Wall","-Wextra","-Wno-unused-parameter","-Wno-invalid-offset","-Wno-attributes","-I"+str(D),"-I"+str(P),"-I"+str(N/"src/mesh/dcCartMesh/lnInclude")]
for inc in ["OpenFOAM/lnInclude","OSspecific/POSIX/lnInclude","meshTools/lnInclude","surfMesh/lnInclude","fileFormats/lnInclude"]:common.append("-I"+str(F/inc))
records=[]
def run(name,cmd):
    start=time.monotonic();r=subprocess.run(cmd,env=env,capture_output=True,text=True)
    (P/(name+".log")).write_text(r.stdout+r.stderr)
    row=dict(name=name,command=cmd,exitCode=r.returncode,elapsed=time.monotonic()-start)
    (P/(name+".json")).write_text(json.dumps(row,indent=2)+"\n");records.append(row)
    print(name,r.returncode,row["elapsed"],r.stderr[-1500:],flush=True);return r.returncode
good=run("actual-of-link-first",common+["-O1",str(P/"actual_of_harness.cpp"),"-o",str(P/"tmp/actual_of_harness"),"-L"+str(W/"lib"),"-L"+str(W/"lib/dummy"),"-Wl,-rpath,"+str(W/"lib"),"-Wl,-rpath,"+str(W/"lib/dummy"),"-lOpenFOAM","-lmeshTools","-lsurfMesh","-lfileFormats","-lPstream","-ldl","-lm"])==0
if good:
    modes="legacy_pair current_live_shape_handles clone_and_append expires immutable_snapshot duplicate facet_missing wrong_region global_bool_only alternate_path nonfinite_budget nonlocal bad_shape stale_boundary false_contract".split()
    for mode in modes:
        good=run("actual-of-"+mode,["taskset","-c","8-15",str(P/"tmp/actual_of_harness"),str(P/("false.dict" if mode=="false_contract" else "true.dict")),mode])==0 and good
# Syntax only: actual production API, not an object/library/mesher build.
good=run("actual-C-syntax-first",common+["-fsyntax-only",str(D/"dcCartCutCells.C")])==0 and good
good=run("actual-APP-syntax-first",common+["-fsyntax-only",str(N/"applications/utilities/mesh/generation/dcCartMesh/dcCartMesh.C")])==0 and good
(P/"actual-api-summary.json").write_text(json.dumps(dict(allPass=good,actualOFTypes=True,businessBuild=False,records=records),indent=2)+"\n")
