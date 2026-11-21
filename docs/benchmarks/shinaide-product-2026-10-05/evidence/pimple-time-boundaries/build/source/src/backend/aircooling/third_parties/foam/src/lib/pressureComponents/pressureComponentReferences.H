/* Pressure references for each disconnected pressure-Laplacian component.
 *
 * regionSplit joins ordinary cyclic and processor faces, including transformed
 * cyclic pairs. AMI/ACMI and other pressure couplings require a different graph
 * and are rejected before computing references, rather than silently overpinning.
 *
 * A physical boundary anchors a component only when its assembled diagonal
 * coefficient is nonzero. This includes Robin boundaries and excludes mixed
 * fields with valueFraction=0 (whose fixesValue() nonetheless returns true).
 */
#ifndef pressureComponentReferences_H
#define pressureComponentReferences_H

#include "regionSplit.H"
#include "processorPolyPatch.H"
#include "cyclicPolyPatch.H"
#include <cmath>

namespace Foam
{
class pressureComponentReferences
{
    const fvMesh& mesh_;
    // solutionControl::dict() returns a value; own it across solver iterations.
    const dictionary dict_;
    labelList regions_;
    labelList defaultCells_;
    labelList referenceCells_;
    labelList anchored_;
    globalIndex globalCells_;
    bool readReference_;
    label preferredCell_;
    label preferredRegion_;
    scalar referenceValue_;
    bool reported_;

public:
    pressureComponentReferences
    (
        const fvMesh& mesh,
        const volScalarField& pressure,
        const dictionary& settings
    )
    :
        mesh_(mesh),
        dict_(settings),
        globalCells_(mesh.nCells()),
        readReference_(false),
        preferredCell_(-1),
        preferredRegion_(-1),
        referenceValue_(0),
        reported_(false)
    {
        label unsupported = 0;
        forAll(pressure.boundaryField(), patchi)
        {
            const polyPatch& poly = mesh.boundaryMesh()[patchi];
            const bool matrixCoupled = pressure.boundaryField()[patchi].coupled();
            const word& fieldType=pressure.boundaryField()[patchi].type();
            // Jump/custom coupled fields can inject affine interface sources
            // outside fvScalarMatrix::source(). They need their own assembly
            // proof before the raw RHS compatibility test below is applicable.
            const bool supported =
                (isA<processorPolyPatch>(poly)
                    && (fieldType=="processor" || fieldType=="processorCyclic"))
                || (isA<cyclicPolyPatch>(poly) && fieldType=="cyclic");
            if ((matrixCoupled && !supported) || (poly.coupled() != matrixCoupled))
            {
                Pout<< "Unsupported pressure component coupling on patch "
                    << poly.name() << " (polyPatch=" << poly.type()
                    << ", pressure=" << pressure.boundaryField()[patchi].type()
                    << ')' << nl;
                unsupported = 1;
            }
        }
        reduce(unsupported, maxOp<label>());
        if (unsupported)
        {
            FatalErrorInFunction
                << "Pressure component references support ordinary cyclic and "
                << "processor coupling only; AMI/ACMI, jump/custom pressure "
                << "fields or mismatched coupling must be handled explicitly "
                << "before this solver can run."
                << exit(FatalError);
        }

        const regionSplit components(mesh, true);
        regions_ = components;
        defaultCells_.setSize(components.nRegions(), labelMax);
        forAll(regions_, celli)
        {
            const label region = regions_[celli];
            defaultCells_[region] = min
            (
                defaultCells_[region],
                globalCells_.toGlobal(celli)
            );
        }
        Pstream::listCombineReduce(defaultCells_, minEqOp<label>());
        referenceCells_.setSize(defaultCells_.size(), -1);
        anchored_.setSize(defaultCells_.size(), 0);
    }

    void constrain
    (
        fvScalarMatrix& equation,
        const volScalarField& pressure,
        const volScalarField& rhok,
        const volScalarField& gh,
        const volScalarField& physicalPressure,
        const surfaceScalarField* predictorFlux = nullptr
    )
    {
        anchored_ = 0;
        scalarField effectiveDiagonal(equation.diag());
        scalarField effectiveSource(equation.source());
        scalarField sourceMagnitude(mag(equation.source()));
        forAll(pressure.boundaryField(), patchi)
        {
            const fvPatchScalarField& patch = pressure.boundaryField()[patchi];
            const labelUList& cells = mesh_.boundary()[patchi].faceCells();
            const scalarField& coefficients = equation.internalCoeffs()[patchi];
            forAll(cells, facei)
            {
                const label celli = cells[facei];
                effectiveDiagonal[celli] += coefficients[facei];
                if (!patch.coupled())
                {
                    const scalar boundarySource =
                        equation.boundaryCoeffs()[patchi][facei];
                    effectiveSource[celli] += boundarySource;
                    sourceMagnitude[celli] += mag(boundarySource);
                    if (coefficients[facei] != 0)
                        anchored_[regions_[celli]] = 1;
                }
            }
        }
        Pstream::listCombineReduce(anchored_, maxEqOp<label>());

        bool freeComponents=false;
        forAll(anchored_, region)
            freeComponents=freeComponents || !anchored_[region];

        // Backward error must include original conservative face contributions.
        // Near divergence-free flow can cancel them to a tiny RHS: scaling only
        // by |source| would falsely reject roundoff from fvc::div's assembly.
        scalarField faceAssemblyMagnitude(anchored_.size(), 0);
        const auto addFaceMagnitude = [&](const surfaceScalarField& flux)
        {
            if (flux.dimensions()!=equation.dimensions())
            {
                FatalErrorInFunction
                    << "Pressure compatibility face scale has incompatible "
                    << "dimensions: " << flux.dimensions() << " versus "
                    << equation.dimensions() << exit(FatalError);
            }
            const labelUList& owner=mesh_.owner();
            const labelUList& neighbour=mesh_.neighbour();
            forAll(flux, facei)
            {
                const label ownRegion=regions_[owner[facei]];
                const label neiRegion=regions_[neighbour[facei]];
                const scalar amount=mag(flux[facei]);
                // Internal source assembly adds once to each adjacent cell.
                if (!anchored_[ownRegion])
                    faceAssemblyMagnitude[ownRegion]+=amount;
                if (!anchored_[neiRegion])
                    faceAssemblyMagnitude[neiRegion]+=amount;
            }
            forAll(flux.boundaryField(), patchi)
            {
                const labelUList& cells=mesh_.boundary()[patchi].faceCells();
                forAll(cells, facei)
                {
                    const label region=regions_[cells[facei]];
                    if (!anchored_[region])
                        faceAssemblyMagnitude[region]+=
                            mag(flux.boundaryField()[patchi][facei]);
                }
            }
        };
        if (freeComponents)
        {
            if (predictorFlux)
                addFaceMagnitude(*predictorFlux);
            // gaussLaplacianScheme adds -V*div(faceFluxCorrection) to source.
            // Owned callers set fluxRequired, retaining that original field.
            if (equation.hasFaceFluxCorrection())
                addFaceMagnitude(*equation.faceFluxCorrectionPtr());
        }

        // fvScalarMatrix::solveSegregated adds only noncoupled boundary sources
        // to source_. Ordinary cyclic/processor terms remain the conservative
        // linear operator. Check each free component BEFORE adding any gauge.
        scalarField sourceSums(anchored_.size(), 0);
        scalarField sourceAbsSums(anchored_.size(), 0);
        scalarField compensation(anchored_.size(), 0);
        scalarField absCompensation(anchored_.size(), 0);
        if (freeComponents)
        {
            forAll(regions_, celli)
            {
                const label region=regions_[celli];
                if (anchored_[region])
                    continue;
                const scalar y=effectiveSource[celli]-compensation[region];
                const scalar t=sourceSums[region]+y;
                compensation[region]=(t-sourceSums[region])-y;
                sourceSums[region]=t;
                const scalar ay=sourceMagnitude[celli]-absCompensation[region];
                const scalar at=sourceAbsSums[region]+ay;
                absCompensation[region]=(at-sourceAbsSums[region])-ay;
                sourceAbsSums[region]=at;
            }
            Pstream::listCombineReduce(sourceSums, plusEqOp<scalar>());
            Pstream::listCombineReduce(sourceAbsSums, plusEqOp<scalar>());
            Pstream::listCombineReduce(faceAssemblyMagnitude, plusEqOp<scalar>());
        }
        forAll(anchored_, region)
        {
            if (anchored_[region])
                continue;
            const scalar scale=sourceAbsSums[region]+faceAssemblyMagnitude[region];
            const scalar tolerance=64*SMALL*scale;
            if (!std::isfinite(sourceSums[region])
                || !std::isfinite(scale)
                || mag(sourceSums[region])>tolerance)
            {
                FatalErrorInFunction
                    << "Incompatible assembled RHS in free pressure component "
                    << region << ": sum=" << sourceSums[region]
                    << ", sumAbs=" << sourceAbsSums[region]
                    << ", faceAssemblyScale=" << faceAssemblyMagnitude[region]
                    << ", roundoffTolerance=" << tolerance
                    << ". A pressure gauge cannot balance imposed sources/fluxes."
                    << exit(FatalError);
            }
            if (!reported_)
                Info<< "Pressure RHS compatibility: component " << region
                    << ", sum=" << sourceSums[region]
                    << ", sumAbs=" << sourceAbsSums[region]
                    << ", faceAssemblyScale=" << faceAssemblyMagnitude[region]
                    << ", roundoffTolerance=" << tolerance << nl;
        }

        bool needsReference = false;
        forAll(anchored_, region)
            needsReference = needsReference || !anchored_[region];
        if (needsReference && !readReference_)
        {
            // Force the dictionary read even if another component has Dirichlet
            // pressure. OpenFOAM's global needReference() otherwise skips it.
            setRefCell(physicalPressure, pressure, dict_,
                preferredCell_, referenceValue_, true);
            preferredRegion_ = returnReduce
            (
                preferredCell_ >= 0 ? regions_[preferredCell_] : -1,
                maxOp<label>()
            );
            readReference_ = true;
        }

        forAll(anchored_, region)
        {
            referenceCells_[region] = -1;
            if (anchored_[region])
                continue;

            const label globalCell = defaultCells_[region];
            label localCell = globalCells_.isLocal(globalCell)
                ? globalCells_.toLocal(globalCell) : -1;
            if (region == preferredRegion_)
                localCell = preferredCell_;
            referenceCells_[region] = localCell;

            if (localCell >= 0)
            {
                scalar weight = effectiveDiagonal[localCell];
                if (weight == 0)
                {
                    // A one-cell, all-Neumann component has a zero operator.
                    // Global assembled RHS compatibility was checked above
                    // with the original conservative face assembly scale.
                    // fvm::laplacian has a negative diagonal. Retain that sign.
                    weight = -1;
                }
                const scalar target = referenceValue_
                    - rhok[localCell]*gh[localCell];

                // Unlike fvMatrix::setReference, this uses the diagonal that
                // solveSegregated will actually use, including coupled boundary
                // coefficients. Raw diag is zero for a cell whose only
                // neighbour resides on another MPI rank.
                equation.diag()[localCell] += weight;
                equation.source()[localCell] += weight*target;
            }
        }

        if (!reported_)
        {
            Info<< "Pressure components: " << anchored_.size()
                << ", boundary-anchored: " << sum(anchored_)
                << ", gauge-referenced: " << anchored_.size()-sum(anchored_)
                << nl;
            reported_ = true;
        }
    }

    void correct
    (
        volScalarField& pressure,
        const volScalarField& rhok,
        const volScalarField& gh,
        volScalarField& physicalPressure
    ) const
    {
        scalarField shifts(anchored_.size(), 0);
        forAll(referenceCells_, region)
        {
            const label celli = referenceCells_[region];
            if (!anchored_[region] && celli >= 0)
                shifts[region] = referenceValue_
                    - (pressure[celli] + rhok[celli]*gh[celli]);
        }
        Pstream::listCombineReduce(shifts, plusEqOp<scalar>());

        forAll(pressure, celli)
            pressure.primitiveFieldRef()[celli] += shifts[regions_[celli]];
        forAll(pressure.boundaryField(), patchi)
        {
            scalarField values(pressure.boundaryField()[patchi]);
            const labelUList& cells = mesh_.boundary()[patchi].faceCells();
            forAll(cells, facei)
                values[facei] += shifts[regions_[cells[facei]]];
            pressure.boundaryFieldRef()[patchi] == values;
        }
        physicalPressure = pressure + rhok*gh;
    }
};
}
#endif
