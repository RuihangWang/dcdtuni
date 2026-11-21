# Independent domain source review — initial findings

This folder records a read-only review and one approved, bounded pure JSON Node probe. It does not certify browser, solver, mesh, USD/SimReady conformance, or end-to-end acceptance. All work stays in the integration checkout and CPU 28–31. No product source, tests, Git, service, or browser was mutated by this reviewer.

## Source findings sent to the author

1. Initial `DomainProject.allEntities` omitted real `server.constructions.coldPlates`, chips, server.faces/patches, rack partitions/RDHX, and CDU internal declarations. Actual LiquidSingle and Shinaide input records identified the paths; the author is adding their types/paths. CP-CU source records and cpCuPairs compatibility records have distinct paths and must not be merged merely by local UID/name.
2. Initial load guard bitwise-narrowed schemaVersion, unlike `decodeDomainProject`'s Number check. A large legal JSON number could pass the first guard and make the second throw. The author is replacing it with one finite safe-integer range guard and returning errors through the load contract.
3. Initial unsupported geometric export gate only examined dictionary members of top-level unknown buckets. Nested unknown containers and arrays/singleton shapes needed the same refusal. The author is broadening enumeration and the gate.
4. New entities expose source references by design. Readonly reference declarations do not freeze records. This matches the existing project store and avoids repeated whole-project deep clones; every future mutating domain method must still run through store.edit/undo/input revision. The present foundation is a derived read view, not an independent editing authority.
5. Actual geometry.cutouts are carried by geometry() but initially absent from the identity inventory. The author is adding explicit geometry feature records. Repeated legacy `rack` bags on unrelated model kinds are preserved declarations, not automatically real rack entities.

## Verified historical load loss

`migration-prototype-probe.cjs` uses the actual shared migrator's CommonJS path and an explicit JSON v3 input. The source SHA is in `v3-prototype-observation.json`. Root and box own `__proto__` extension fields changed only their respective object prototypes and disappeared from JSON serialization. An unrelated plain object's prototype was unchanged. Current-schema v5 passthrough retained the own field. This is data loss plus per-record prototype alteration, not evidence of global Object.prototype pollution.

The historical migration legitimately changes schema and records; no byte-identical v3→v5 claim is made. Extension semantics and source identity/physics will be audited separately after the author freezes final source.
