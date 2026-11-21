import numpy as np,glob,os,sys
from collections import defaultdict
case=sys.argv[1]
planes=[(1,float(v)) for v in sys.argv[2].split(',')]+[(0,float(v)) for v in sys.argv[3].split(',')]+[(2,float(v)) for v in sys.argv[4].split(',')]
area=defaultdict(lambda: defaultdict(float))
for f in glob.glob(f'case/{case}/Mesh/constant/triSurface/*.stl'):
    cur=None; tri=[]
    for line in open(f,errors='ignore'):
        t=line.split()
        if not t: continue
        if t[0]=='solid': cur=t[1]
        elif t[0]=='vertex':
            tri.append(tuple(map(float,t[1:4])))
            if len(tri)==3:
                a=np.array(tri); tri=[]
                for ax,v in planes:
                    if np.all(np.abs(a[:,ax]-v)<1e-4):
                        area[(ax,v)][(os.path.basename(f),cur.split('_')[0] if not cur.startswith(('Wall','Floor','Top')) else cur)]+=0.5*np.linalg.norm(np.cross(a[1]-a[0],a[2]-a[0]))
for p,d in area.items():
    print(p, round(sum(d.values()),4), {k:round(v,3) for k,v in sorted(d.items(),key=lambda x:-x[1])[:8]})
