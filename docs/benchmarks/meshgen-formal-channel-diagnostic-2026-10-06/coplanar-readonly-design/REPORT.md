# Normal UI b5a0cbc5: S10 cell457 read-only diagnosis and guarded design

Actual failure: S10 kind1 closure, not ingest rejection. Native cells457/463/469/475 each have norm(Sum Sf)=0.00390625. S10 sums each current face oriented area vector and refuses the smallest failing compacted ci (Cartesian.cpp:31285-31291,32679-32693). tolClose=1e-10*H^2, H=1.5 from native box/root formula; positive tolerance2.25e-10. Printed0.000000 is decimal rounding. S9d excludes open cells (25116), matching actual kind1Skip=4.

Reports remain separate: scratch/dc_report.json is dc17-ingest-mapping/v1; scratch/constant/dc_report.json is native stageS7. Current runner's report-path fix belongs to another task; this task copied but did not modify it.

## Raw STL and candidate

Independent ASCII STL analysis:44 triangles/22 names,0 open edges,8 incidence-three edges on z=0/z=1 channel-room seams. Components:outer channel/room32 triangles,thermal block12. Thus native S7 label Channel_supply_Back_patch with32 triangles refers to a connected shell, not the inlet's2 triangles alone. This explains shell census geometry, not cell457 causality.

Thermal_block_Bottom:y=.5,x/z=[.25,.375],area=.125^2=.015625. Native rootorigin=-.25,H=1.5. Actual kept levels:L3=56,L4=1280; L4 edge=.09375,split line=.3125. Four bottom footprint rectangles each have area=.0625^2=.00390625,matching all four failure norms. y=.5 is a native grid plane. This is a specific geometric candidate only: no cell-ID or signed-residual binding exists.

Relevant source consistency surface:triFaceChord skips all-on-plane triangles(4441-4445); S7 shared dcFaceComponents clips coplanar footprints and excludes coverage(4875-4943); S8 no-chord construction emits whole box face(11815-11821),while wall tiles separately clip/emit authored patches(12468-12483,12664-12691). S8 chord-present route uses cutFaceByChainsAll. Thermal vertical-wall triangles can produce edge chords on this plane,so the no-chord route MUST NOT be asserted as the actual route. Actual binding must select which component/side/tile branch failed.

Old kind1 JSON flag lookup drifts after compaction(source31568-31592); actual aligned census says all4 coplanar,none0. Family signature does not diagnose a missing face.

## Conditional minimal repair

First obtain existing diagnostics through an authorized normal-service/normal-React job with the same owned input/STL/formal binary; no standalone CLI business replay. Bind compacted cells to signed face areas,points,patches,leaf boxes,source triangles and S8 route.

If binding confirms box-face/thermal-wall overlap,confine repair to S8 coplanar ownership:reuse shared footprint enumeration(including chord geometry),emit exact uncovered fluid complement as box/internal faces,and give authored wall tiles the covered footprint exactly once with original patch and outward winding. Preserve holes/multiple components,shared canonicalization and existing tolerance. If evidence instead shows omitted tile/side-classification failure,repair that recorded emitter/owner. No concrete source diff is justified now.

Keep S10 closure/cone/centroid checks and rc1 unchanged. No warn-only export,tolerance increase,STL jitter,role/seed dodge,or residual-balancing cap.

## Existing diagnostic environment: design only

Service environment:CART_DBG6=1 CART_S10_CENSUS=1 CART_A4_CENSUS=1 CART_DBG_K1ALL=1.
Retain formal binary pin/production knobs/owned input/resource policy. CURRENT serial runner Popen:9231-9235 has no env replacement; source supports inherited service env. Later actual inheritance must be recorded. Flags activate by presence,even value0/empty(95-105); absence disables.

Do not set CART_DBG,CART_DBG_ALL,CART_DBG9,CART_E1_GATE_WARN_ONLY:31221-31222 changes fastS10 path; last lowers gate. DBG6/S10_CENSUS/A4_CENSUS are absent from that selector.

CART_DBG_CELL uses atol:one integer only,not comma list. CART_DBG_CEN parses one x,y,z,tolerance1e-6; integer/coordinate predicates OR(569-605). Phase1 expects origCi or leaf-box centre; S10 provides compacted ci and volume centroid. Do not conflate them.

Pre-refusal output:
- DC-S10K:31500-31523 dumps all first6 kind1 cells independent of filter. Current4 failures fit. Header:ci,nfaces,level,volume cen,splitPiece. Every face:nbr,patch,point IDs/coordinates,oriented Sf,home.
- DC-S10R:32285-32318 prints top8 worst + top8 clean:ci,aligned family,signed residual,h,vol,cen. All current4 fit.
- DC-K1A:31534-31542 prints every kind1,but its family is old drifting lookup; use aligned S10R.
- DC-S10D:32648-32675 recomputes signed residual/full faces for first terminal failure. Limit64 does NOT mean64 failures:strict return follows first.
- DC-A4F:31731-31739,31800-31818,31969-32006 gives origCi,boxCen,boxHalf ONLY for exShort example. No guarantee it binds457 or all4;if lost classification,anchor may be absent.
- DC-E3D-PATH:14210-14223 requires explicit integer/coordinate filter,gives phase1 ci,leafID,dcSB/fallback,boxCen.
- DC-E3D-BF:14161-14197 gives selected OPEN side-built birth faces:points,patch,boxside,Sf.
- DC-E3D-CH:11440-11455 gives selected chord source triangle/face/endpoints/normal; fragment and emission rows have their local guards.

DBG6 is not globally bounded by CART_DBG_CELL:some DC-SB/tileNone/etc sites ignore selection. Cannot promise total byte/line count. First normal job can expose all4 S10 face tables; later authorized normal job can target acquired phase1 origCi/boxCen for S8 trace. No existing four-cell list selector; no guaranteed four-cell leafBox binding in one job.

## Limits/preservation

Primary57 lacks actual failing-cell faces/points,volume cen,signed residuals,leafBox mappings; no polyMesh exported. Candidate is not proven cell457 cause. No GPU/rendering claim.

Reports,four STLs,owned cfdrun,fullLog and native sources are copied byte-exact and checked against ROOT-verified primary manifest. Current Cartesian.cpp equals frozenSHA6200ab2a...; CURRENT runnerSHA8fd23969... is stored separately. Only this new directory was written. CPU8-15/CUDAempty used for stdlib reading,hashing,arithmetic and evidence writing. No source edit/build/native meshing/checkMesh/test/API/browser/service/physics/Git execution.
