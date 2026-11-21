import json
from pathlib import Path
pts=[(0,0,-.1),(1,.2,-.1),(1,1,-.1),(0,1,-.1),(0,0,0),(1,0,0),(1,1,0),(0,1,0),(.499,0,0),(.499,.1,0),(.499,1,0),(0,.1,0),(1,.1,0)]
exactTol=1e-5; minShare=.02
def clean(points,axis,plane):
    span=max(p[axis] for p in points)-min(p[axis] for p in points)
    return all(not(exactTol<abs(p[axis]-plane)<minShare*span) for p in points)
a=pts[0]; b=pts[1]; t=.499
p=tuple(a[i]+t*(b[i]-a[i]) for i in range(3))
result={'convex_half_spaces':['x>=0','x<=1','z>=-.1','z<=0','y>=0','y>=.2*x-2*z-.2','y<=1'],'initial_points':pts,'initial_cleanX':clean(pts,0,.499),'initial_cleanY':clean(pts,1,.1),'new_intersection':p,'new_point_distance_to_Y':abs(p[1]-.1),'child_point_set_cleanY':clean(pts+[p],1,.1),'exactTol':exactTol,'minShare':minShare,'meaning':'An ancestor candidate must revalidate the current point set before each subsequent seam plane. This script audits the geometric guard, not a native mesh acceptance.'}
assert result['initial_cleanX'] and result['initial_cleanY'] and not result['child_point_set_cleanY']
print(json.dumps(result,indent=2))
