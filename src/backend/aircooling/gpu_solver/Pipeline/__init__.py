"""Method families.

One subpackage per way this repository seeks a solution.  Each consumes the
shared substrate (FVdomain, FVsolver, SparseSolver, Utils, ...) through narrow
seams and never imports another family.

    CoupledNewton    steady square Newton solve, assembled Jacobian + GMRES/AMG
    ODIL             FIFO time-window optimisation (Adam and Newton-Krylov)
    PCNO             GNN trained against the FVM residual
    NeuralOperator   GNN trained against OpenFOAM labels
    MMS              manufactured-solution verification of the operators
"""
