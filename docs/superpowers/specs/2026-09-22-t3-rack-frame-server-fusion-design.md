# T3 — rack frame + server fusion (design, 2026-09-22)

Status: DESIGN FOR DECISION. Nothing implemented yet. Scoped from the export
code and a Base604 dcCart mesh; the user's intent is quoted from
`BHUMANS/DctwinBasic.md:24`.

## What the user asked for

> server 的宽度一定是和 RACK frame interior 面宽度保持一致 … server 的左右面的
> three.js 对象或者说 stl 对象还是可以存在且导出。只不过我们生成网格的时候，要把
> frame 本身和 server 本体融合到一起成为一个固体，而不要在 server 的左右侧面与 RACK
> frame 的 interior 面之间重合的地方生成 patch，因为这里没有流体 … server 现在还是
> 回到一个方盒子的状态，而不是由零散的顶部 + inlet/outlet 面片构成的一个东西。

So: keep exporting the server's sides, but at mesh time (1) the frame and the
server body form ONE solid, (2) no patch where a server side lies on the frame
interior, (3) the server is a closed box again, not four loose sheets.

## What the export emits today (evidence)

- Frame: up to 20 open sheets merged under one solid name `<rack>_frame`
  (`src/frontend/shared/js/geometry_scene.js:2520-2681`): outer shell,
  front/back border strips, inner cavity walls `inner_left` (X = slotX) /
  `inner_right` (X = slotX + slotWidth), `inner_floor` (Y = marginBottom, only
  when marginBottom > 0), `inner_ceiling`, and the inlet/outlet cavity sheets.
  Doors are separate 1 mm-offset sheets. Bucket `merged_rack_frame.stl`.
- Server: a closed six-face box (`geometry_scene.js:2687`). Names from
  `getServerFaceExportName` (`geometry_pure.js:519`): fan server
  `<stem>_inlet_patch` / `_outlet_patch` / `_top` / `_bottom` / `_left` /
  `_right`; non-fan `_front` / `_back` instead of inlet/outlet.
- Left/right are routed to a private bucket `merged_server_interface_walls.stl`
  (`stl_naming.js:293-336`) and left OUT of the manifest by
  `_buildServerFacesExport`, which enumerates only four faces
  (`cfdrun_build.js:136-151`). The bucket sits in `constant/triSurface/` but
  no `snappyHexMeshDict` entry references it (checked on Base604: 0 hits), so
  neither mesher ever sees a server side.
- Partitions `<rack>_u<i>_partition` are emitted only when the project keeps
  them; Base604 exports none.

What the mesher therefore sees (Base604, dcCart np8, 2026-09-22):
`27 surfaces (5 carving, 22 baffle)` — every rack frame and every server
face is a BAFFLE sheet, none is a carving solid. Rack A17's patches in
`processor0/constant/polyMesh/boundary`: `_frame` 5066 faces; per server
`_inlet_patch` (patch) / `_outlet_p0` (patch) / `_outlet_wall` / `_top` /
`_bottom` (walls). The server interior is FLUID, a through-channel closed
sideways by the frame's `inner_left` / `inner_right` sheets. The
"disappearing" side walls are not a hole: the frame already walls that plane.

## Exact coincidences that remain

| server face | frame sheet on the same plane | when |
|---|---|---|
| left / right | `inner_left` / `inner_right` | always (x0 = slotX, x1 = slotX + slotWidth, `geometry_pure.js:489-497`) — but not exported to the mesher |
| bottom | `inner_floor` (Y = marginBottom) or `outer_bottom` (Y = 0) | bottom-most server (startUnit = 0) |
| top | `inner_ceiling` (Y = marginBottom + slotAreaHeight) | server reaching the top slot |
| top / bottom | `<rack>_u<i>_partition` at i = startUnit + span - 1 / startUnit - 1 | only when partitions are exported (not Base604) |
| inlet / outlet | door sheet 1 mm away | near-coincident (not exact) — the classic weld already handles the 1 mm |

Two coincident baffle sheets in one plane give the cut-cell pass two zone
faces on one cell face; today the seam/coalesce machinery ("boundary faces
renamed after the facet they lie in", "sliver absorbed across a patch / zone
difference") resolves them case by case. This is the fusion the user means:
decide ONE owner per plane so the mesher never has to.

## Two ways to read "one solid"

**A. Sheet model kept, coincident sheets de-duplicated at export (small).**
The server stays a through-channel of baffles. In `_buildSingleServerFace`
(`geometry_scene.js:2770`) mark a server face `exportName: null` (still
rendered, not written to STL) when it lies on a frame sheet:

- `bottom` when `y0 == marginBottom` (or `== 0` with no margin) or a
  partition exists at `startUnit - 1`;
- `top` when `y1 == marginBottom + slotAreaHeight` or a partition exists at
  `startUnit + span - 1`;
- `left` / `right`: already excluded from the manifest; make the exclusion
  explicit at build time instead of by bucket routing.

Pair each top/bottom drop with the matching removal in
`_buildServerFacesExport` (`cfdrun_build.js:151`) so no boundary binding
references a missing solid, and re-point the server's top/bottom heat BC to
the frame sheet it now shares (`<rack>_frame` is one merged wall patch, so a
per-server heat flux would need the frame split by plane — see B).
Cost: a day. Effect: removes the exact duplicates; leaves the server as
sheets (contradicts the "box" wording but matches the through-channel physics
the fan BCs rely on).

**B. Server as a carved solid box fused into the frame (the wording).**
Export the six server faces as ONE closed solid `<stem>` (carving), and let
the mesher remove the cells inside it. The fan becomes a pair of boundary
patches on that solid (`_inlet_patch` = fluid leaves the room into the
server, `_outlet_patch` = fluid re-enters, with the heat rise), which is the
standard 6Sigma / commercial-DC-CFD server model. The frame's inner cavity
sheets between servers stay as baffles; where the server solid touches them
the solid wins (a solid face against a baffle in the same plane: the baffle's
faces there border removed cells and are simply not created).

Consequences that must be decided before coding:

1. BCs: today `_inlet_patch` / `_outlet_p0` are internal baffle patches
   (fan momentum jump across a through-channel). As solid-box faces they
   become an outlet/inlet pair with a prescribed flow rate and ΔT — the
   runner's `bridge_bc` / `fields.py` fan-server branch needs a second mode,
   and the liquid-cooling / AI postprocess readers that look for the
   through-channel patches need the new names.
2. The frame's `inner_left` / `inner_right` sheets inside a server's span
   are then INSIDE the solid; the clip pass ignores baffle faces in removed
   cells, but the export should still stop emitting them there to keep the
   patch acceptance (`mesh_acceptance`) from declaring regions with no faces.
3. `mesh_acceptance` and the snappy path must accept the new patch set, and
   the cfdrun / cfdproj schema needs a per-server `model: throughChannel |
   solidBox` switch so old projects keep meshing exactly as before.
4. The DAML testbed models real devices with measured through-flow; the
   solid-box model changes their results, so the acceptance gate for B is
   "the three cases solve and their return-air temperatures stay within the
   experimental band", not bit-identity.

Cost: a week end to end (export, schema, runner BCs, both meshers, tests,
re-validation of the 16 dataset cases).

## Recommendation

Do A now (it is what the mesher needs to stop seeing duplicate planes, and it
is what "no patch where server and frame coincide" literally asks for), and
put B behind the `model` switch as a separate work package once the user
confirms the fan-BC change is wanted. The user's "回到一个方盒子" sentence reads
as B; the "没有流体" sentence reads as A. Both cannot be true for a fan server
whose channel carries the room air, so this needs the user's call.

## Open questions for the user

1. Fan servers: keep the through-channel (air passes through the server
   region, fan as an internal jump) or switch to a solid box with
   inlet/outlet boundary patches?
2. Non-fan equipment: solid box (today: baffle sheets with a temperature)?
3. When a server's top/bottom coincides with a frame sheet, which patch
   carries the server's heat BC — the server face (kept) or the frame sheet
   (server face dropped)?
