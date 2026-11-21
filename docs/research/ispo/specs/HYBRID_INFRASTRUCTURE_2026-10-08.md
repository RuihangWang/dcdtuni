# Hybrid infrastructure: user decision and current contracts

The user confirmed fan-assisted liquid servers on2026-10-08: keep liquidServer
identity, cold plates, pipes and authored server airflow.40901 owns Shinaide
air-mesh and CFD convergence repair. Pro6000 now focuses on the energy model,
one-dimensional/network air model, evaluator contracts and post-training.
Both previous Pro mesh attempts are terminal failures; no new mesh/CFD repair
is dispatched here. They remain evidence for the previous surface-heat model.

## Physical and numerical interfaces

- Source project and model semantics are separate explicit identities. Display
  meta.enabled does not remove a construction from the production CFD export;
  real exporter source states this. The three containment sheets remain physical.
- The56 liquid-server models retain their original positive airflows, summing
  12.656m3/s. Current production nonFan export does not consume those values.
  The new fan-assisted research interface must consume them explicitly, preserve
  cold-plate topology, and report that its new physical model is distinct.
- Per-device IT heat equals air heat plus accepted liquid removal plus solid
  storage. The current steady case has zero storage. Existing84kW total IT is
  an explicit mock budget; the21/63kW split has independently verified DAE and
  heat-ledger evidence for that prescribed load.
- The actual four ACU settings are heterogeneous: m2=4.2m3/s at20C,
  m6=4.0at21C, m14=3.0at19C and m18=3.5at19C; total14.7m3/s.
  Values must agree through editor patches, cfdrun input/boundary and resolved
  registry. An earlier first-unit extrapolation to4x4.2 was incorrect.
- One-dimensional server streams use the same UID heat and positive flow.
  Room transport/mixing remains a declared approximation. Preserve real floor,
  openings, containment and source-presence semantics when defining zones.
  If a fully mixed baseline has zero geometry derivative, report that property;
  do not invent position dependence to make a gradient look informative.
- L0 can supply scalar/component outputs and validated continuous derivatives.
  Its nodal temperatures are not a three-dimensional field, so it cannot supply
  target-CFD I2. That later contract needs a coarse/learned field and matched
  target residual evaluation.

## One shared energy model

The new src/agent/scenario_hybrid/energy.py combines air fan electricity,
secondary liquid-pump electricity and ONE common chiller/primary-pump/rejection
plant. Total IT appears once as the design-PUE denominator. Server fan watts
remain inside the declared IT budget; airflow alone does not identify watts.

Air coils use a conservative common return-water setpoint constrained by every
coil's hot-end pinch. The common chilled-water supply satisfies both air and
liquid branches. Warmer CDU supply boundaries require explicitly idealized
return-water recirculation, with local and fresh heat-capacity flow and mixing
fractions reported. Fixed target heat and measured room closure are kept
separate, preventing numerical loss of heat from reducing the charged plant load.

Secondary pump electricity uses declared model hydraulic head times volumetric
flow divided by a source-labelled efficiency. The present DAE thermal ledger
does not include pump dissipative heat; this scope is explicit. Other auxiliary
heat is outside the current thermal domain. Capacity, valve authority, branch
water-flow limits and vendor calibration remain separate checks.

Six CPU implementation checks pass, including independent heat/mixing
identities, a single IT denominator, catalog-domain refusal, pure-liquid limit,
pump power units/provenance, and a two-step-size energy-input derivative check.
These are algebraic infrastructure gates, not a Shinaide whole-plant validation.
The test's4x3m3/s fixture is not the actual Shinaide operating point.
Actual flows exceeding an assumed catalog must be reported out-of-domain,
never clipped or renamed as a vendor rating.

## Pump-speed model boundary requiring correction before a speed study

The current frozen DAE implements H=N^2*(H0-a*Q^2), with fixed nominal a.
A nominal curve H_ref(Q)=H0-a*Q^2 under fixed-impeller affinity scaling gives
H_N(Q)=N^2*H_ref(Q/N)=N^2*H0-a*Q^2.
The two agree at N=1, the already accepted case, and generally differ away
from it. The actual head convention and parameter definition must be resolved
before claiming physical pump-speed sensitivities. The present energy
interface accepts explicit head data and does not silently repair the DAE.
Reference: [Hydraulic Institute pump curves and affinity rules](https://datatool.pumps.org/pump-fundamentals/pump-curves.html).

## Method comparison required by the user

MFPG-PPO is the principal existing multi-fidelity policy baseline, including
its budget-aware version. It already handles correlated paired/uncoupled data,
PPO actor-critic updates, coefficient monitoring and fallback. Its paper
explicitly discusses bias from LF re-synchronization; this is distinct from
bugs found in the predecessor's released code.

ISPO must demonstrate the value of choosing among information interfaces
I0/I1/I2/I3, including matched physical path derivatives and residual-based
acquisition with recorded positive inclusion probabilities, under full measured
cost. Multi-fidelity control variates, LAX/AIPW, adjoints and budget allocation
are established components. Compare the published MFPG-PPO mechanism and a
matched-backbone MFPG variant, and isolate each extra information channel.
Reference: [MFPG-PPO, arXiv2610.02505v1](https://arxiv.org/html/2610.02505v1).
