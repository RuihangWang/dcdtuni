# third_parties/foam — dcdtuni custom OpenFOAM solvers

Solver source that this repo builds itself, so a clone is reproducible instead
of depending on a binary someone once installed into a conda prefix.

```bash
cd src/backend/aircooling/third_parties/foam
./deploy.sh          # check env -> build -> smoke test
```

Builds **non-destructively** into `platforms/<WM_OPTIONS>/bin/`. The stock
`buoyantBoussinesqSimpleFoam` in the conda prefix is never touched, so the two
coexist and can be A/B compared. The runner resolves the binary from
`platforms/` by path (`STLGenRunner._resolve_solver_binary`), so nothing needs
to be on `PATH`.

Layout mirrors `third_parties/dcCartMesh/` — same `env.sh` / `Allwmake` /
`deploy.sh` mechanism, different output directory. Keep the two in step.

```
foam/
├── env.sh          source this first — wires wmake to the conda OpenFOAM v2412
├── Allwmake        build:  ./Allwmake [-j]
├── Allwclean       clean
├── deploy.sh       fresh-clone entry point: prerequisites -> build -> verify
├── src/
│   ├── lib/
│   │   ├── explicitBoundaryWrite/writeBCFields.H
│   │   └── pressureComponents/
│   └── applications/
│       ├── solvers/buoyantBoussinesqSimpleFoam_new/    steady (the runner's steady default)
│       ├── solvers/buoyantBoussinesqPimpleFoam_new/    transient (the runner's transient default)
│       └── utilities/writeExplicitBC/                  explicit boundary values after reconstructPar
└── template/       Jinja2 dictionaries used by the runner (not built)
```

Build output lands in `platforms/<WM_OPTIONS>/bin/`: `buoyantBoussinesqSimpleFoam_new`,
`buoyantBoussinesqPimpleFoam_new` and `writeExplicitBC`. The runner uses the
`*_new` solvers by default (`DCDTUNI_SOLVER_OVERRIDE` or
`simulationConfig.solverName` override it). When the steady one is not built it
warns and falls back to `PATH`; the transient one refuses to run unbuilt.

---

## buoyantBoussinesqSimpleFoam_new

Steady buoyant incompressible solver. Identical physics to
`buoyantBoussinesqSimpleFoam`; the difference is what reaches disk.

### The problem it fixes

OpenFOAM writes a boundary patch by asking the patch field to serialise itself,
and the most common types serialise to nothing but their own name. From a real
result file (`case/Base_leakage/log/.../964/T`):

```
    ACU_1_Front        { type zeroGradient; }        <- no values at all
    Room_Floor         { type zeroGradient; }        <- no values at all
    ACU_1_Bottom_patch { type fixedValue; value uniform 288.15; }
```

`U` is the same story with `noSlip`. The values exist inside the solver — they
are simply not in the file, and every reader is expected to re-derive them.
Anything consuming the raw files (an ML pipeline, a custom post-processor, a
diff between two runs) finds a hole exactly where the wall data should be.

After each write step this solver re-writes every `AUTO_WRITE` volField with
the boundary values spelled out.

### Modes

Selected in `controlDict` (the runner's templates set `preserveType`):

```
explicitBoundaryValues
{
    mode        preserveType;   // or fixedValue
}
```

| mode | what a `zeroGradient` patch becomes | restartable |
|---|---|---|
| `preserveType` | `type zeroGradient;` **+** `value nonuniform List<scalar> N (...)` | **yes** |
| `fixedValue` (solver default) | `type fixedValue;  // original: zeroGradient` + values | no — see below |

`fixedValue` is the original behaviour and stays the solver's built-in default
so existing external pipelines keep the bytes they expect. It is the more
destructive of the two: an ACU inlet written as
`flowRateInletVelocity { volumetricFlowRate constant 4; }` comes back as a
frozen `fixedValue`, so a restart *runs* but solves a different problem.
`preserveType` gives a reader exactly the same numbers with none of that risk,
which is why the runner asks for it.

Constraint and coupled patches — `processor`, `cyclic` (the `createBaffles`
master/slave pairs), `empty`, `wedge`, `symmetry`, `symmetryPlane` — are always
written untouched. Their type *is* the data; overwriting it would break mesh
coupling.

### Other option

```
explicitBoundaryValues
{
    writeFirstIteration true;   // default false
}
```

Persists the very first SIMPLE iteration whatever `writeControl`/`writeInterval`
say — the ML dataset build needs it as the t=1 input. Off by default, so the
solver is otherwise a drop-in replacement for the stock one.

### Verified

`deploy.sh` runs the `hotRoom` tutorial for 5 iterations and asserts that no
physical patch in any written field is value-less, and that `preserveType` left
`zeroGradient` in place. On Base_leakage (8 procs, 5 iterations, reconstructed):

```
Room_Floor                { type zeroGradient;  value nonuniform List<scalar> 4266 (...) }
Default_Rack_1_frame      { type zeroGradient;  value nonuniform List<scalar> 18628 (...) }
Raised_Floor_copy_patch_* { type cyclic; }        <- coupled, left alone by design
```

Restart from either mode's output was checked to run to completion.

Note: `reconstructPar` re-serialises with `Field::writeEntry`, which collapses a
genuinely uniform patch back to `value uniform X`. That is still complete
information — the writer's always-nonuniform guarantee holds for the
per-processor files.

### Transient component gauges

`buoyantBoussinesqPimpleFoam_new` is the runner's transient default. It retains
the official OpenFOAM v2412 velocity and temperature equations and adds the
same pressure-component references as the SIMPLE build, plus explicit boundary
values after each write. Static meshes with ordinary cyclic/processor coupling
are supported; declared mesh motion/topology and unknown/AMI pressure coupling
fail with a clear scope message. An absent repo build fails before MPI launch.

SIMPLE also writes explicit boundary values after its convergence loop exits:
`simpleControl` can perform a final stock `writeAndEnd()` that otherwise removes
the last physical boundary values. The final MPI regression asserts `End` and
explicit U/T patch values after that exit.

Source provenance, isolated build hashes and MPI regression receipts are in
`docs/benchmarks/pressure-component-gauge-2026-10-05/`.
