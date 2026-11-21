# Liquid equipment visuals and port geometry

The React liquid editor draws generic engineering illustrations for CDU cabinets and liquid-cooled rack manifolds. These shapes communicate service doors, HMI location, handles, plinth/feet, paired connections, quick-connect collars and short rack branch hoses. They do not represent a verified PEK02 vendor model, and they do not reuse the CDU 70 liquid-to-air facade.

## Geometry authority

The `.cfdproj` model remains authoritative for location, size, quaternion, scale, collision/layout bounds and exported solids. `liquidEquipmentVisuals.ts` adds a child group under that structural model only while React liquid mode is active. Every decorative mesh is tagged `exportExcluded` and `selectable: false`, is absent from `renderMeshes`, and has a no-op raycast. STL/CFD export, physical graph identity and layout placement therefore continue to use the structural model.

Small features are clamped against the model dimensions. Pipe and manifold radii use the configured engineering diameter where available; the visual code does not enlarge mains or invent internal CDU hydraulics. The cabinet shell is a neutral powder-coated generic enclosure, with subdued blue supply and red return collars.

The model root owns the detail group's GPU resources. `sceneBuilder.clear()` and targeted model rebuilds already dispose the full root through `geometryPure.disposeRenderObject`, so detail geometry and materials follow the same lifecycle without a second registry.

## Port transform contract

`geometryPure.liquidEquipmentPortFrame(model, port, end)` is the common port contract. It returns local and world position/direction. World positions use the same anchor, scale and normalized quaternion as `applyModelTransformToObject`, including pitch and roll rather than assuming yaw-only or axis-aligned equipment.

CDU supply and return retain the previous unrotated renderer centers: the rear centerline at 85% cabinet height, with the return 50 mm below supply. Decorative necks extend inward and end at that logical center.

Rack endpoints follow `cooling.liquidManifold`: layout (`split`, `both-left`, `both-right`), mounting face, configured pipe diameters, spacing, safe bottom and usable U-slot height. The top endpoint is the rendered quick-connect center and the graph manifold-node position. Snap targets, automatic pipe rendering and `.liquidrun` graph compilation call this same transform. Graph branch waypoints are built from transformed rack-local points, so displayed endpoints and physical path lengths remain consistent after equipment rotation.

## Intentional limits

The visual rack branches are illustrative short hoses and do not create graph edges. The graph topology still comes from configured servers, cold plates, pipes and valves. No unmodeled valves, pump internals, heat exchanger internals or vendor-specific register geometry is implied by the illustration.
