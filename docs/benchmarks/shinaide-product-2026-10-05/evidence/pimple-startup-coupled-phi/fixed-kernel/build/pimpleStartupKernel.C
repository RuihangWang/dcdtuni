// Pytest-only startup/Euler/pressure-projection kernel, never a product solver.
#include "fvCFD.H"
#include "coupledFvPatch.H"
int main(int argc, char *argv[])
{
    #include "setRootCaseLists.H"
    #include "createTime.H"
    #include "createMesh.H"
    // Generated from the exact production createFields.H U read/startup block.
    #include "startupVelocityRead.H"
    surfaceScalarField phi
    (
        IOobject("phi", runTime.timeName(), mesh,
            IOobject::NO_READ, IOobject::NO_WRITE),
        mesh.Sf() & dimensionedVector("flow", dimVelocity, vector(1,0,0))
    );
    const surfaceScalarField initialPhi(phi);
    volScalarField pressure(IOobject("p", runTime.timeName(), mesh,
        IOobject::MUST_READ, IOobject::NO_WRITE), mesh);
    surfaceScalarField rAUf(IOobject("rAUf", runTime.timeName(), mesh),
        mesh, dimensionedScalar("rAUf", dimTime, runTime.deltaTValue()));
    mesh.setFluxRequired(pressure.name());
    ++runTime;
    const surfaceScalarField ddtFlux(fvc::ddtCorr(U, phi));
    const surfaceScalarField predictor(phi + rAUf*ddtFlux);
    fvScalarMatrix equation(fvm::laplacian(rAUf, pressure)==fvc::div(predictor));
    equation.solve();
    const surfaceScalarField corrected(predictor-equation.flux());
    const volScalarField divergence(fvc::div(corrected));
    OFstream rows(runTime.path()/"startup-kernel-faces.csv");
    rows.precision(17);
    forAll(mesh.boundary(), patchi)
    {
        const fvPatch& patch=mesh.boundary()[patchi];
        forAll(patch, i)
        {
            const vector cache=U.oldTime().boundaryField()[patchi][i];
            const vector own=U.oldTime().boundaryField()[patchi].patchInternalField()()[i];
            const vector sf=mesh.Sf().boundaryField()[patchi][i];
            rows<< patch.name()<<' '<<patch.coupled()<<' '<<i<<' '
                <<cache.x()<<' '<<cache.y()<<' '<<cache.z()<<' '
                <<own.x()<<' '<<own.y()<<' '<<own.z()<<' '
                <<sf.x()<<' '<<sf.y()<<' '<<sf.z()<<' '
                <<initialPhi.boundaryField()[patchi][i]<<' '
                <<ddtFlux.boundaryField()[patchi][i]<<' '
                <<predictor.boundaryField()[patchi][i]<<' '
                <<corrected.boundaryField()[patchi][i]<<nl;
        }
    }
    OFstream cells(runTime.path()/"startup-kernel-cells.csv");
    cells.precision(17);
    forAll(divergence,i)
        cells<<i<<' '<<mesh.V()[i]*divergence[i]<<nl;
    Info<<"End"<<endl;
    return 0;
}
