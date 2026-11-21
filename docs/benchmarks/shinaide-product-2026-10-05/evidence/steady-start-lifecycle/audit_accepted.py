#!/usr/bin/env python3
"""Recheck preserved actual two-rank artifacts without running or mocking CFD."""
import hashlib,json,math,re
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def location0(text):
    return re.sub(r'(\blocation\s+)"[^"]*"',r'\1"0"',text,count=1)

def internal(text):
    match=re.search(r'\binternalField\s+(.*?)\n\s*boundaryField\b',text,re.S)
    assert match
    return match.group(1).strip()

def scalar_values(statement):
    match=re.fullmatch(r'uniform\s+(\S+);',statement)
    if match:
        return [float(match.group(1))]
    match=re.fullmatch(r'nonuniform\s+List<scalar>\s+(\d+)\s*\((.*?)\)\s*;',statement,re.S)
    assert match
    result=[float(v) for v in match.group(2).split()]
    assert len(result)==int(match.group(1))
    return result

def case(name,expected_cells,iteration,nonzero):
    directory=ROOT/name
    receipt_name='execution-receipt.json' if (directory/'execution-receipt.json').exists() else 'air-run-execution-receipt.json'
    input_name='final-input.cfdrun' if (directory/'final-input.cfdrun').exists() else 'input.cfdrun'
    receipt=json.loads((directory/receipt_name).read_text())
    report=json.loads((directory/'steady_start.json').read_text())
    assert receipt['status']=='accepted'
    assert report['actual_n_procs']==2
    assert report['convergence']['verdict']=='STEADY_CONVERGED'
    assert report['convergence']['converged_iteration']==iteration
    assert hashlib.sha256((directory/input_name).read_bytes()).hexdigest()==receipt['result']['cfdrunSha256']
    for item in receipt['result']['meshFiles']:
        assert hashlib.sha256((directory/item['path']).read_bytes()).hexdigest()==item['sha256'],item['path']
    for item in receipt['result']['fieldFiles']:
        rank,_,field=item['path'].split('/')
        assert hashlib.sha256((directory/rank/'final0.02'/field).read_bytes()).hexdigest()==item['sha256'],item['path']
    counts=[];phi=[]
    for rank in ('processor0','processor1'):
        owner=(directory/rank/'constant/polyMesh/owner').read_bytes()
        match=re.search(rb'nCells:\s*(\d+)',owner[:4096]);assert match
        counts.append(int(match.group(1)))
        seed=directory/rank/'seed0';steady=directory/rank/('steady'+str(iteration));final=directory/rank/'final0.02'
        seed_phi=(seed/'phi').read_text()
        assert location0(seed_phi)==location0((steady/'phi').read_text())
        assert re.search(r'class\s+surfaceScalarField;',seed_phi)
        phi.extend(scalar_values(internal(seed_phi)))
        for field in ('T','U','alphat','epsilon','k','nut','p','p_rgh'):
            assert internal((seed/field).read_text())==internal((steady/field).read_text()),(rank,field)
        for path in final.iterdir():
            text=path.read_text()
            assert not re.search(r'(?i)(?<!\w)[+-]?(?:nan|inf(?:inity)?)(?!\w)',text),str(path)
            numbers=re.findall(r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?',internal(text))
            assert all(math.isfinite(float(v)) for v in numbers),str(path)
        temperature=(seed/'T').read_text()
        hot=re.search(r'Thermal_block_Front\s*\{(.*?)\}',temperature,re.S)
        assert hot and 'fixedValue' in hot.group(1) and '305.15' in hot.group(1)
        assert '295.15' in temperature
    assert counts==expected_cells
    assert (max(abs(v) for v in phi)>0)==nonzero
    quality=(directory/'strict-quality.sse.log').read_text()
    assert 'checkMesh -allTopology -allGeometry -parallel' in quality
    assert 'Mesh OK.' in quality and 'accepted (0 failed checks)' in quality
    simple=(directory/'log.steady_start').read_text()
    marker=re.search(r'SIMPLE solution converged in\s+(\d+)\s+iterations',simple)
    assert marker and int(marker.group(1))==iteration
    pimple=(directory/'log.mpi_solver').read_text()
    assert re.search(r'^End\s*$',pimple,re.M) and 'Time = 0.02' in pimple
    assert 'PIMPLE: converged' in pimple
    return {'artifact':name,'verified':True,'globalCells':sum(counts),'steadyIteration':iteration,
            'finalTimeS':0.02,'wholePhiExact':True,'phiNonzeroProven':nonzero,
            'maxInternalAbsPhi':max(abs(v) for v in phi)}

result=[case('accepted-conduction-mpi2',[247,248],153,False),
        case('accepted-nonzero-channel-mpi2',[255,256],337,True),
        case('accepted-nonzero-channel-precise-mpi2',[255,256],337,True),
        case('accepted-current-simple-nonzero-mpi2',[255,256],337,True)]
print(json.dumps(result,indent=2))
