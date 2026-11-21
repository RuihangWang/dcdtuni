// Test-only pressure kernel: the production Laplacian and reference helpers.
#include "fvCFD.H"
#include "mixedFvPatchFields.H"
#ifndef DCDTUNI_LEGACY_PRESSURE_REFERENCE
#include "pressureComponentReferences.H"
#endif
int main(int argc, char *argv[])
{
    #include "setRootCaseLists.H"
    #include "createTime.H"
    #include "createMesh.H"
    IOdictionary settings(IOobject("gaugeTestDict", runTime.system(), mesh,
        IOobject::MUST_READ, IOobject::NO_WRITE));
    volScalarField p_rgh(IOobject("p_rgh", runTime.timeName(), mesh,
        IOobject::MUST_READ, IOobject::AUTO_WRITE), mesh);
    const scalar gravity = settings.getOrDefault<scalar>("gravity", 0);
    volScalarField gh(IOobject("gh", runTime.timeName(), mesh),
        mesh, dimensionedScalar("zero", dimPressure/dimDensity, 0));
    gh.primitiveFieldRef() = gravity * mesh.C().component(vector::Y)();
    forAll(gh.boundaryField(), patchi)
        gh.boundaryFieldRef()[patchi] == gravity
            * mesh.C().boundaryField()[patchi].component(vector::Y)();
    volScalarField rhok(IOobject("rhok", runTime.timeName(), mesh),
        mesh, dimensionedScalar("one", dimless, 1));
    volScalarField p(IOobject("p", runTime.timeName(), mesh,
        IOobject::NO_READ, IOobject::AUTO_WRITE), p_rgh + rhok*gh);
    mesh.setFluxRequired(p_rgh.name());
#ifdef DCDTUNI_LEGACY_PRESSURE_REFERENCE
    label pRefCell = 0; scalar pRefValue = 0;
    setRefCell(p, p_rgh, settings, pRefCell, pRefValue);
#else
    pressureComponentReferences references(mesh, p_rgh, settings);
#endif
    scalarField rawFirst(mesh.nCells(), 0), effectiveFirst(mesh.nCells(), 0), addedFirst(mesh.nCells(), 0);
    for (label iter=0; iter<3; ++iter)
    {
        if (iter == 1 && settings.found("toggleFraction"))
        {
            const label patchi=mesh.boundaryMesh().findPatchID("closedWalls");
            refCast<mixedFvPatchScalarField>
            (
                p_rgh.boundaryFieldRef()[patchi]
            ).valueFraction() = settings.get<scalar>("toggleFraction");
            p_rgh.boundaryFieldRef()[patchi].evaluate();
        }
        fvScalarMatrix eqn(fvm::laplacian(p_rgh));
        const word sourceMode=settings.getOrDefault<word>("sourceMode","none");
        const scalar amplitude=settings.getOrDefault<scalar>("sourceAmplitude",1);
        autoPtr<surfaceScalarField> conservativeFlux;
        if (sourceMode=="conservativeFlow")
        {
            conservativeFlux.reset(new surfaceScalarField
            (
                "conservativeFlow", mesh.Sf()
                & dimensionedVector("transport",dimAcceleration,
                    settings.getOrDefault<vector>("transport",vector(0.123456789,0.3,0.8)))
            ));
            eqn.source() -= mesh.V()
                * fvc::div(conservativeFlux())().primitiveField();
            if (settings.found("netSource"))
            {
                globalIndex allCells(mesh.nCells());
                if (allCells.isLocal(0))
                    eqn.source()[allCells.toLocal(0)] +=
                        settings.get<scalar>("netSource");
            }
            Info<< "CONSERVATIVE_FLOW_RHS sum=" << gSum(eqn.source())
                << ", sumAbs=" << gSumMag(eqn.source())
                << ", internalFaceScale=" << gSumMag(conservativeFlux())
                << nl;
        }
        forAll(eqn.source(), celli)
        {
            const scalar x=mesh.C()[celli].x();
            if (sourceMode=="balancedConnected")
                eqn.source()[celli] += x<1 ? amplitude : -amplitude;
            else if (sourceMode=="uniform")
                eqn.source()[celli] += amplitude;
            else if (sourceMode=="anchoredOnly" && x<1)
                eqn.source()[celli] += amplitude;
        }
        if (eqn.hasFaceFluxCorrection())
            Info<< "NONORTH_CORRECTION_SCALE "
                << gSumMag(*eqn.faceFluxCorrectionPtr()) << nl;
        scalarField effective(eqn.diag());
        forAll(p_rgh.boundaryField(), patchi)
        {
            const labelUList& cells=mesh.boundary()[patchi].faceCells();
            forAll(cells, i)
                effective[cells[i]] += eqn.internalCoeffs()[patchi][i];
        }
        if (!iter) { rawFirst=eqn.diag(); effectiveFirst=effective; }
#ifdef DCDTUNI_LEGACY_PRESSURE_REFERENCE
        eqn.setReference(pRefCell, getRefCellValue(p_rgh, pRefCell));
#else
        references.constrain(eqn, p_rgh, rhok, gh, p,
            conservativeFlux.valid() ? &conservativeFlux() : nullptr);
#endif
        if (!iter) addedFirst=eqn.diag()-rawFirst;
        eqn.solve();
        p = p_rgh + rhok*gh;
#ifdef DCDTUNI_LEGACY_PRESSURE_REFERENCE
        if (p_rgh.needReference())
        {
            p += dimensionedScalar("p", p.dimensions(),
                pRefValue-getRefCellValue(p, pRefCell));
            p_rgh = p-rhok*gh;
        }
#else
        references.correct(p_rgh, rhok, gh, p);
#endif
        const surfaceScalarField solvedFlux(eqn.flux());
        scalar maxFlux = 0;
        forAll(solvedFlux, facei)
            maxFlux=max(maxFlux,mag(solvedFlux[facei]));
        forAll(solvedFlux.boundaryField(), patchi)
            forAll(solvedFlux.boundaryField()[patchi], facei)
                maxFlux=max(maxFlux,mag(solvedFlux.boundaryField()[patchi][facei]));
        reduce(maxFlux,maxOp<scalar>());
        Info<< "GAUGE_FLUX " << maxFlux << nl;
    }
    OFstream result(runTime.path()/"gauge-kernel-result.csv");
    result.precision(17);
    forAll(p_rgh, celli)
        result<< mesh.C()[celli].x() << ' ' << mesh.C()[celli].y()
            << ' ' << mesh.C()[celli].z() << ' ' << p_rgh[celli]
            << ' ' << p[celli] << ' ' << rawFirst[celli] << ' '
            << effectiveFirst[celli] << ' ' << addedFirst[celli] << nl;
    Info<< "End" << endl;
    return 0;
}
