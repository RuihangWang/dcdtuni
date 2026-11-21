# L0 network prototype status

l0_network.py implements coupled pressure/enthalpy conservation and an implicit
derivative. l0_geometry.py builds declared effective passages from true exported
STL ports and supports rigid x/z translations. This is an independent lumped
model; its capture/bypass fractions are unfitted. It supplies no HF field/I2.

The83-node/32server/8ACU smoke on the earlier geometry prototype converged and
closed326.4kW. A backward call produced finite control/layout derivatives. Formal
two-size FD, translation/flux invariants and matched HF calibration are still open.
Do not treat this smoke as a verified gradient oracle or a main learning result.

Primary framework reference: EnergyPlus24.2 AirflowNetwork Engineering Reference,
https://bigladdersoftware.com/epx/docs/24-2/engineering-reference/airflownetwork-model.html
The independent model is not the EnergyPlus implementation or its validation.

The current adapter rejects containment and liquid projects until their explicit
leakage/heat-transfer topology is implemented and checked. Shinaide mixed cooling
requires actual accepted Q_air/Q_liquid; the all-air prototype cannot supply it.
