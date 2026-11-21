
from pathlib import Path
import copy,json,subprocess,sys,hashlib,os
import numpy as np
import pytest
Q=Path(__file__).resolve().parent;A=Q.parents[1]
H=A/'.codex-tmp/shared-point-native-feasibility-20261005'
sys.path.insert(0,str(H))
import select_small_mode as M
import finite_direction_proposal as S
import compact_formula_evaluator as E
HEADER=A/'src/backend/aircooling/third_parties/dcCartMesh/src/mesh/dcCartMesh/dcCartCutCells/dcCartFinitePointRepair.H'

@pytest.fixture(scope='session')
def binary():
    p=Q/'kernel_harness'
    subprocess.run(['g++','-std=c++14','-O2','-Wall','-Wextra','-Werror',
                    '-I',str(HEADER.parent),str(Q/'kernel_harness.cpp'),'-o',str(p)],
                   check=True,capture_output=True,text=True)
    return p

def call(binary,s):
    return subprocess.check_output([str(binary)],input=s,text=True).strip().split()

def selector_input(doc,target):
    pids=sorted(map(int,doc['points']));fids=sorted(map(int,doc['faces']))
    pi={p:i for i,p in enumerate(pids)}
    xyz=[doc['points'][str(p)] for p in pids]
    out=f'select {len(pids)} {len(fids)} {fids.index(target)}\n'
    out+=' '.join(format(x,'.17g') for p in xyz for x in p)+'\n'
    for f in fids:
        row=doc['faces'][str(f)]
        protected=row['patch'] is not None or row.get('sourceZone')!=-1
        out+=f"{len(row['pointIDs'])} {int(protected)} "+' '.join(str(pi[p]) for p in row['pointIDs'])+'\n'
    return out,pids

@pytest.mark.parametrize('rank,target',[(1,1468973),(3,1512587)])
def test_actual_selector_from_original_geometry(binary,rank,target):
    raw=json.loads((H/f'finite-rank{rank}-ratio-result.json').read_text())
    wire,pids=selector_input(raw['oldDocument'],target);ans=list(map(int,call(binary,wire)))
    expected=M.select(raw['oldDocument'],target)
    got=[pids[ans[1]]]+([pids[ans[2]],pids[ans[3]]] if ans[0]==4 else [])
    assert got==expected['movingPoints']
    assert ans[0]==len(expected['motionBasis'][0])
    assert ans[4]==(-1 if expected['pairAxis'] is None else expected['pairAxis'])

def test_one_ulp_plane_difference_refuses_pair(binary):
    raw=json.loads((H/'finite-rank1-ratio-result.json').read_text());doc=copy.deepcopy(raw['oldDocument'])
    p=raw['movingPoints'][1];doc['points'][str(p)][1]=float(np.nextafter(doc['points'][str(p)][1],1))
    wire,_=selector_input(doc,1468973);ans=list(map(int,call(binary,wire)))
    assert ans[0]==3 # AP remains eligible, rotating pair is unsupported

def test_zone_protection_refuses_pivot(binary):
    raw=json.loads((H/'finite-rank3-ratio-result.json').read_text());doc=copy.deepcopy(raw['oldDocument'])
    pivot=raw['movingPoints'][0]
    for f in doc['faces'].values():
        if pivot in f['pointIDs']:f['sourceZone']=2;break
    wire,_=selector_input(doc,1512587);ans=list(map(int,call(binary,wire)))
    assert ans[0]==0

@pytest.mark.parametrize('seed',range(12))
def test_projection_differential(binary,seed):
    rng=np.random.default_rng(seed);n=3+seed%2;m=20
    aa=rng.normal(size=(m,n));bb=np.abs(rng.normal(size=m));desired=rng.normal(size=n)*.1
    expected=S.project(desired,aa,bb)
    wire=f'project {n} {m} 384\n'+' '.join(map(str,desired))+'\n'
    for row,b in zip(aa,bb):wire+=' '.join(map(str,row))+' '+str(b)+'\n'
    ans=call(binary,wire)
    assert bool(int(ans[0]))==expected[1]
    assert np.allclose(np.asarray(ans[2:],float),expected[0],rtol=0,atol=1e-10)

def test_projection_fail_closed_edges(binary):
    assert call(binary,'edge\n')==['00100','0']

@pytest.mark.parametrize('rank,target',[(1,1468973),(3,1512587)])
def test_cpp_finite_search_actual_frozen_callback(binary,rank,target):
    raw=json.loads((H/f'finite-rank{rank}-ratio-result.json').read_text())
    choice=M.select(raw['oldDocument'],target)
    valid=S.C.validate_internal_polygon if rank==1 else S.V.validate_structure
    ev=E.evaluator(raw['oldDocument'],choice['movingPoints'],motion_basis=choice['motionBasis'],
                   structural_validator=valid,target_faces={target})
    process=subprocess.Popen([str(binary)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    process.stdin.write(f"propose {ev['dimension']}\n");process.stdin.flush()
    history=[];target_index=ev['faceIDs'].index(target)
    while True:
        line=process.stdout.readline().split()
        assert line
        if line[0]=='done':
            requests=int(line[1]);params=np.asarray(line[4:],float);break
        assert line[0]=='eval'
        q=np.r_[np.asarray(line[1:],float),0.]
        values=ev['fast'](q);cons=ev['constraints'](q)
        objective=-float(values[2][target_index])/.01
        process.stdin.write(f'1 {objective:.17g} {len(cons)} '+' '.join(format(x,'.17g') for x in cons)+'\n')
        process.stdin.flush();history.append(q.tolist())
    process.stdin.close();assert process.wait(timeout=5)==0
    assert requests==len(history)<=256
    # This compiles/executes only the pure kernel, not the business mesher.
    # Formula callbacks use original input, never prior optimizer coordinates.
    q=np.r_[params*.95,0.]
    cand,_,_=ev['actual'](q)
    audit=S.C.authorize_internal_polygon(raw['oldDocument'],cand,choice['movingPoints']) if rank==1 else S.V.authorize(raw['oldDocument'],cand,choice['movingPoints'])
    assert audit['mathematicalLocalCandidateAccepted']
    assert audit['newCounts']['ratio']==0
    assert audit['oldCounts']['D']==audit['newCounts']['D']
    (Q/f'cpp-rank{rank}-callback-replay.json').write_text(json.dumps({
        'rank':rank,'headerSHA256':hashlib.sha256(HEADER.read_bytes()).hexdigest(),
        'inputSHA256':hashlib.sha256((H/f'finite-rank{rank}-ratio-result.json').read_bytes()).hexdigest(),
        'requests':requests,'parameters':params.tolist(),'evaluationParameters':history,
        'fraction':.95,'audit':audit,'nativeAcceptance':False,
        'cppPureKernelWithPythonFormulaOracle':True},indent=2)+'\n')

def test_old_endpoint_new_hard_failure_remains_rejected():
    raw=json.loads((H/'finite-projection-result.json').read_text())
    end=next(x for x in raw['results'] if x['fraction']==1)
    assert not end['audit']['mathematicalLocalCandidateAccepted']
    assert end['audit']['newCounts']['D']>end['audit']['oldCounts']['D']

def test_actual_cell_zone_core_outside_reject_other_zone_does_not(binary):
    assert call(binary,'zones\n')==['0110']

def test_original_endpoint_hard_threshold_no_epsilon(binary):
    # The preserved actual endpoint is below .001 by 1.96e-12.
    assert call(binary,'roles 1 .001 0\n.00101353236\n.0009999999980392809\n')==['0']

def test_hard_role_improvement_cannot_hide_new_passing_failure(binary):
    # Fewer bad cells is insufficient when an original passing role regresses.
    assert call(binary,'roles 3 .001 0\n.0002 .0003 .002\n.002 .002 .0009\n')==['0']

def test_existing_bad_severity_must_not_worsen(binary):
    assert call(binary,'roles 2 .05 0\n.01 .2\n.009 .2\n')==['0']
    assert call(binary,'roles 2 .05 0\n.01 .2\n.02 .2\n')==['1']

def test_no_new_external_optimizer_dependency():
    text=HEADER.read_text()
    assert 'scipy' not in text and 'Eigen' not in text and 'SLSQP' not in text
