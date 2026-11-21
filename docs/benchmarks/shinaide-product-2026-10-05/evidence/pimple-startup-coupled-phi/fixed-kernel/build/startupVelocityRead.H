Info<< "Reading field U\n" << endl;
volVectorField U
(
    IOobject
    (
        "U",
        runTime.timeName(),
        mesh,
        IOobject::MUST_READ,
        IOobject::AUTO_WRITE
    ),
    mesh
);

// A processor patch without value starts from its LOCAL internal field.
// Exchange its true neighbour before Euler can copy that cache into U.oldTime().
// Evaluate only coupled patches; preserve the configured physical boundaries.
U.boundaryFieldRef().evaluateCoupled<coupledFvPatch>();

