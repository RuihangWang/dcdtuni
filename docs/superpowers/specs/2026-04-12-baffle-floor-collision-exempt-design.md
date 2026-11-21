# Baffle/Floor Collision Exemption Design

## Goal

Exempt `baffle` and `floor` model types from the drag-time collision detection system so they can be freely positioned inside the data center room, overlapping with other objects. These model types are designed to be co-planar with or embedded in other geometry; the downstream auto-cut (CSG boolean) step trims shared surfaces at export time.

## Architecture

A single constant set `COLLISION_EXEMPT_KINDS` added near the existing collision detection functions. Both collision check functions filter out exempt models bidirectionally: if **either** party in a collision pair is an exempt kind, the pair is skipped.

No changes to model data structures, serialization, undo/redo, or any other subsystem.

## Code Changes

### File: `STLGeneratorForOpenFoam/web/STL_generate.html`

#### 1. New constant (near collision functions, ~L4590)

```javascript
const COLLISION_EXEMPT_KINDS = new Set(['baffle', 'floor']);
```

#### 2. `_analyticalCollisionCheck(movingModels, movingIds)` (~L4590-4603)

- Filter `movingModels` to exclude exempt kinds before building OBB list. If all moving models are exempt, return `false` immediately (no collision).
- In the inner loop over stationary models, skip any model whose `kind` is in `COLLISION_EXEMPT_KINDS`.

#### 3. `modelGroupMoveWouldCollide(modelIds)` (~L4682-4697)

- Same bidirectional filter: skip exempt moving models and exempt stationary models.
- If all moving models are exempt, return `false` immediately.

### What does NOT change

- **`areModelsWithinProjectBounds()`**: baffle/floor are still constrained within the room boundary. Only inter-object collision is exempted, not bounds checking.
- **Collision epsilon** (1e-4): unchanged.
- **Model data structure**: no new properties added.
- **Undo/redo, serialization, export**: unaffected.
- **All other model types** (`box`, `rack`, `wall`, `acu`, `custom`, etc.): collision behavior unchanged.

## Test Plan

| # | Scenario | Expected |
|---|----------|----------|
| 1 | Drag a baffle through a box | Movement allowed |
| 2 | Drag a box into a stationary baffle | Movement allowed |
| 3 | Drag a floor into a rack | Movement allowed |
| 4 | Drag a rack into a stationary floor | Movement allowed |
| 5 | Drag box A into box B | Movement blocked (unchanged) |
| 6 | Drag a rack into another rack | Movement blocked (unchanged) |
| 7 | Drag a baffle outside room bounds | Movement blocked by bounds check (unchanged) |
| 8 | Multi-select: baffle + box, drag into another box | Box-to-box collision still detected |
| 9 | Undo after placing baffle inside room | Reverts correctly |

## Risks

- **Low**: A user might accidentally place a baffle/floor in a wrong position without collision feedback. Mitigated by the existing undo system and the fact that baffle/floor placement is always intentional (users create them specifically to partition the room).

## Non-goals

- Per-model collision toggle (YAGNI).
- Exempting other model types (wall, custom, etc.) from collision.
- Changing bounds checking behavior.
