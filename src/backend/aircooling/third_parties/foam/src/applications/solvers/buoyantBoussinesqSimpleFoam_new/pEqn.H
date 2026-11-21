{
    volScalarField rAU("rAU", 1.0/UEqn.A());
    volVectorField HbyA(constrainHbyA(rAU*UEqn.H(), U, p_rgh));

    surfaceScalarField phiHbyA
    (
        "phiHbyA",
        fvc::flux(HbyA)
    );

    // SIMPLEC (SIMPLE { consistent yes; }): simpleFoam's consistent form with
    // the pressure and buoyancy force G = grad(p_rgh) + gh*grad(rhok) treated
    // together. rAtU replaces rAU in the pressure equation and the corrector,
    // and the old G is put back with (rAtU - rAU), so at convergence
    // U = rAU*(H - G), the SIMPLE fixed point.
    tmp<volScalarField> rAtU(rAU);

    if (simple.consistent())
    {
        rAtU = 1.0/(1.0/rAU - UEqn.H1());
        const surfaceScalarField Gf
        (
            (fvc::snGrad(p_rgh) + ghf*fvc::snGrad(rhok))*mesh.magSf()
        );
        const surfaceScalarField dAf(fvc::interpolate(rAtU() - rAU));
        phiHbyA += dAf*Gf;
        HbyA += (rAtU() - rAU)*fvc::reconstruct(Gf);
    }

    surfaceScalarField rAUf("rAUf", fvc::interpolate(rAtU()));

    tUEqn.clear();

    surfaceScalarField phig(-rAUf*ghf*fvc::snGrad(rhok)*mesh.magSf());

    MRF.makeRelative(phiHbyA);

    adjustPhi(phiHbyA, U, p_rgh);

    phiHbyA += phig;

    // Update the pressure BCs to ensure flux consistency
    constrainPressure(p_rgh, U, phiHbyA, rAUf, MRF);

    while (simple.correctNonOrthogonal())
    {
        fvScalarMatrix p_rghEqn
        (
            fvm::laplacian(rAUf, p_rgh) == fvc::div(phiHbyA)
        );

        pressureReferences.constrain(p_rghEqn, p_rgh, rhok, gh, p, &phiHbyA);

        p_rghEqn.solve();

        if (simple.finalNonOrthogonalIter())
        {
            // Calculate the conservative fluxes
            phi = phiHbyA - p_rghEqn.flux();

            // Explicitly relax pressure for momentum corrector
            p_rgh.relax();

            // Correct the momentum source with the pressure gradient flux
            // calculated from the relaxed pressure
            U = HbyA + rAtU()*fvc::reconstruct((phig - p_rghEqn.flux())/rAUf);
            U.correctBoundaryConditions();
            fvOptions.correct(U);
        }
    }

    #include "continuityErrs.H"

    p = p_rgh + rhok*gh;

    pressureReferences.correct(p_rgh, rhok, gh, p);
}
