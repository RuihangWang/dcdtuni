from pathlib import Path
import json,unittest,copy
from collections import defaultdict
N=Path(__file__).resolve().parent
ns={'__file__':str(N/'bounded_flip.py')}
exec((N/'bounded_flip.py').read_text().split('timedout=False')[0],ns)
X=ns['X'];B=ns['B'];assemble=ns['assemble'];partition=ns['exact_partition'];pareto=ns['pareto']
class SemanticTests(unittest.TestCase):
 def test_actual_global_domain_avoids_hidden_neighbour_alias(self):
  d=json.loads((N/'result.json').read_text())
  row=next(r for r in d['trials'] if r['cells']==[508849,508851])
  doc=row['oldDocument'];rankCount=row['actualRankCellCount']
  compactStart=max(map(int,doc['cells']))+1
  referenced={v for f in doc['faces'].values() for v in [f['owner'],f['neighbour']] if v is not None}
  self.assertTrue(set(range(compactStart,compactStart+3))&referenced)
  self.assertFalse(set(row['newDocument']['children'])&referenced)
  self.assertTrue(all(c>=rankCount for c in row['newDocument']['children']))
 def fixture(self):
  pts={0:[0,0,0],1:[2,0,0],2:[0,2,0],3:[.5,.5,1],4:[.5,.5,-1]}
  oldsets=[[3,0,1,2],[4,0,1,2]];incident=defaultdict(list)
  for c,ids in enumerate(oldsets):
   for poly in X.outward_faces(ids,pts):incident[tuple(sorted(poly))].append((c,poly))
  doc={'points':{str(k):v for k,v in pts.items()},'faces':{},'cells':{'0':{'faces':[],'sourceParent':10},'1':{'faces':[],'sourceParent':11}},
       'children':[0,1],'outsideCells':[],'apexWrittenPoint':0,'originalApex':pts[0]}
  shell=[];inside=[]
  for f,(key,rows) in enumerate(sorted(incident.items())):
   owner,poly=rows[0];nei=rows[1][0] if len(rows)==2 else None
   doc['faces'][str(f)]={'owner':owner,'neighbour':nei,'pointIDs':poly,'patch':None if nei is not None else {'name':'walls','type':'wall'},'sourceBase':f,'sourceZone':-1}
   for c,_ in rows:doc['cells'][str(c)]['faces'].append(f)
   (inside if nei is not None else shell).append(f)
  return pts,oldsets,doc,inside,shell
 def test_exact_23_32_roundtrip_shell_volume_moment(self):
  pts,oldsets,doc,inside,shell=self.fixture()
  pre=B.preflight_23([pts[i] for i in [0,1,2]],[pts[i] for i in [3,4]])
  self.assertTrue(pre['strictInterior'])
  newsets=[[3,4,0,1],[3,4,1,2],[3,4,2,0]]
  p=partition(oldsets,newsets,pts);self.assertTrue(p['Vexact'] and p['M1exact'] and p['allNewPositive'])
  cand,mappings,internal=assemble(doc,inside,shell,newsets,2,7)
  self.assertEqual(len(internal),3)
  for f in shell:self.assertEqual(doc['faces'][str(f)]['pointIDs'],cand['faces'][str(f)]['pointIDs'])
  back,_,newinside=assemble(cand,internal,shell,oldsets,5,10)
  self.assertEqual(len(newinside),1)
  for f in shell:
   for k in ['pointIDs','patch','sourceZone','sourceBase']:
    self.assertEqual(doc['faces'][str(f)][k],back['faces'][str(f)][k])
 def test_shared_edge_not_strictly_interior(self):
  pre=B.preflight_23([[0,0,0],[2,0,0],[0,2,0]],[[1,1,1],[1,1,-1]])
  self.assertFalse(pre['strictInterior'])
 def test_new_cells_inherit_every_passing_cell_and_face_role(self):
  old={'cells':[{'cell':0,'D':.002},{'cell':1,'D':.0005}],'faces':[{'face':10,'weight':.2},{'face':11,'weight':.01}]}
  new={'cells':[{'cell':2,'D':.0007},{'cell':3,'D':.0007},{'cell':4,'D':.0007}],'faces':[{'face':12,'weight':.04}]}
  p=pareto(old,new,[0,1],[2,3,4],[10,11],[12])
  self.assertEqual(len(p['D']['passingRoleFailures']),3)
  self.assertEqual(p['weight']['passingRoleFailures'][0]['oldPassingRoles'],[10])
if __name__=='__main__':unittest.main(verbosity=2)
