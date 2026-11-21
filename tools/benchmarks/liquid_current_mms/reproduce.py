#!/usr/bin/env python3
"""Reproduce the full current Shinaide manufactured benchmark inside dcdtuni.

Preparation uses the actual React compiler and independently generated forcing.
--verify additionally executes both original product grids and independent BE
recurrences, then enforces the unchanged registered whole-trajectory criteria.
"""
from pathlib import Path
import argparse, datetime, hashlib, json, os, shutil, signal, subprocess, sys, time

HERE = Path(__file__).resolve().parent
CASE_REL = Path('case/verification/shinaide-glycol25-full-mms-20261013')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def infer_repo():
    for parent in HERE.parents:
        if (parent / '.git').exists():
            return parent
    raise ValueError('Pass --repo-root for a source draft outside its final tools path')

def run(command, work, environment, label, timeout):
    started = time.monotonic()
    with (work / (label + '.stdout.log')).open('wb') as out, (work / (label + '.stderr.log')).open('wb') as err:
        proc = subprocess.Popen(command, cwd=environment['DCTWIN_MMS_REPO'], env=environment,
                                stdout=out, stderr=err, start_new_session=True)
        timed_out = False
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                code = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                code = proc.wait(timeout=10)
    row = {'command':command, 'pid':proc.pid, 'returncode':code,
           'timedOut':timed_out, 'elapsed_s':time.monotonic()-started,
           'stdout':str(work/(label+'.stdout.log')), 'stderr':str(work/(label+'.stderr.log'))}
    (work / (label + '.EXECUTION.json')).write_text(json.dumps(row, indent=2)+'\n')
    print(json.dumps({'phase':label, 'returncode':code, 'timedOut':timed_out,
                      'elapsed_s':row['elapsed_s']}), flush=True)
    if code or timed_out:
        raise RuntimeError('Phase failed; original logs retained: ' + label)
    return row

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root',type=Path)
    parser.add_argument('--case-dir',type=Path)
    parser.add_argument('--frontend-deps',type=Path,help='Optional existing React dependency directory; default is this clone')
    parser.add_argument('--output',type=Path,help='New exclusive output directory under this repository')
    parser.add_argument('--verify',action='store_true',help='Run both complete registered grids after preparation')
    args=parser.parse_args()
    repo=(args.repo_root or infer_repo()).resolve()
    case=(args.case_dir or repo/CASE_REL).resolve()
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d_%H%M%S_%f_UTC')
    work=(args.output or repo/'_tmp/liquid-current-mms'/stamp).resolve()
    if not work.is_relative_to(repo) or work==repo:
        raise ValueError('All benchmark output must stay inside this dcdtuni checkout')
    work.mkdir(parents=True,exist_ok=False)
    for name in ['inputs','compiled','reference','results','source-snapshot']:
        (work/name).mkdir()
    registry=json.loads((case/'PREREGISTRATION.json').read_text())
    for name,contract in [('Baseline.cfdproj','physicalBasisProject'),('GUI-executed.liquidrun','physicalBasisExecutedInput')]:
        source=case/name
        if sha(source)!=registry[contract]['sha256']:
            raise ValueError('Changed registered physical basis: '+str(source))
        shutil.copyfile(source,work/'inputs'/name)
    shutil.copyfile(case/'PREREGISTRATION.json',work/'PREREGISTRATION.json')
    rows=[]
    backend_sources=[p for p in (repo/'src/backend/liquidcooling').rglob('*') if p.is_file() and p.suffix in ('.py','.json','.yaml','.yml')]
    backend_sources += [repo/rel for rel in ['src/backend/aircooling/__init__.py','src/backend/aircooling/runner/__init__.py','src/backend/aircooling/runner/cfdrun_schema.py']]
    for source in sorted(backend_sources):
        if '__pycache__' in source.parts:
            continue
        target=work/'source-snapshot'/source.relative_to(repo)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
        rows.append({'path':str(source),'snapshot':str(target),'sha256':sha(source),'bytes':source.stat().st_size})
    (work/'SOURCE-MAP-backend.json').write_text(json.dumps({'scope':'Actual current checkout liquid backend Python sources copied by value; no product edits','rows':rows},indent=2)+'\n')
    env=dict(os.environ)
    env.update(DCTWIN_MMS_REPO=str(repo),DCTWIN_MMS_WORK=str(work),CUDA_VISIBLE_DEVICES='',
               PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',
               NUMEXPR_NUM_THREADS='1',TMPDIR=str(work),TMP=str(work),TEMP=str(work),
               XDG_CACHE_HOME=str(work/'cache'),TORCHINDUCTOR_CACHE_DIR=str(work/'torchinductor'))
    if args.frontend_deps:
        env['DCTWIN_MMS_FRONTEND_DEPS']=str(args.frontend_deps.resolve())
    else:
        env.pop('DCTWIN_MMS_FRONTEND_DEPS',None)
    start=time.monotonic()
    execution={'status':'preparing_inputs_no_numerical_acceptance','steps':[],
               'startedUTC':datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'repo':str(repo),'work':str(work),'CUDA_VISIBLE_DEVICES':'',
               'preregSHA256':sha(work/'PREREGISTRATION.json')}
    (work/'PORTABLE-EXECUTION.json').write_text(json.dumps(execution,indent=2)+'\n')
    execution['steps'].append(run(['node',str(HERE/'compile_project.mjs'),str(work/'inputs/Baseline.cfdproj')],work,env,'compile-baseline',120))
    shutil.copyfile(work/'SOURCE-MAP-react.json',work/'SOURCE-MAP-react-baseline.json')
    execution['steps'].append(run([sys.executable,'-B',str(HERE/'generate_current_full_mms.py')],work,env,'generate-independent-forcing',180))
    projects=[str(work/'inputs'/('Shinaide_Current_Glycol25_FullMMS_'+g['tag']+'.cfdproj')) for g in registry['grids']]
    execution['steps'].append(run(['node',str(HERE/'compile_project.mjs'),*projects],work,env,'compile-derived',180))
    execution['steps'].append(run([sys.executable,'-B',str(HERE/'verify_prepared.py')],work,env,'verify-complete-input-identities',180))
    if args.verify:
        study_start=time.monotonic(); study={'status':'running_numerical_runs_original_gates','steps':[]}
        for grid in registry['grids']:
            for name,phase in [('run_product.py','product'),('run_recurrence.py','independent-recurrence')]:
                remaining=1800-(time.monotonic()-study_start)
                if remaining<=0:
                    raise TimeoutError('Original 1800 s study deadline exceeded')
                row=run([sys.executable,'-B',str(HERE/name),grid['tag']],work,env,grid['tag']+'-'+phase,min(900,remaining))
                study['steps'].append(row)
                (work/'STUDY-EXECUTION.json').write_text(json.dumps(study,indent=2)+'\n')
        study.update(status='completed_numerical_runs_original_gates',elapsed_s=time.monotonic()-study_start)
        (work/'STUDY-EXECUTION.json').write_text(json.dumps(study,indent=2)+'\n')
        execution['steps'].append(run([sys.executable,'-B',str(HERE/'assess.py')],work,env,'assess-original-full-study-criteria',120))
        result=json.loads((work/'CURRENT-FULL-MMS-RESULTS.json').read_text())
        if result['status']!='PASS_registered_current_full1693_MMS':
            raise RuntimeError('Registered numerical criteria failed; all evidence retained')
        execution['status']=result['status']
    else:
        execution['status']='prepared_real_projects_and_complete_input_admission_no_numerical_acceptance'
    for row in rows:
        if sha(Path(row['path']))!=row['sha256'] or sha(Path(row['snapshot']))!=row['sha256']:
            raise RuntimeError('Backend source changed during benchmark')
    execution['elapsed_s']=time.monotonic()-start
    (work/'PORTABLE-EXECUTION.json').write_text(json.dumps(execution,indent=2)+'\n')
    print(json.dumps({'status':execution['status'],'work':str(work),'elapsed_s':execution['elapsed_s']}),flush=True)

if __name__=='__main__':
    main()
