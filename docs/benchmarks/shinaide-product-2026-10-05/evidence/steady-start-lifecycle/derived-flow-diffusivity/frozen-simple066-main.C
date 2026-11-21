/*---------------------------------------------------------------------------*\
  =========                 |
  \\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox
   \\    /   O peration     |
    \\  /    A nd           | www.openfoam.com
     \\/     M anipulation  |
-------------------------------------------------------------------------------
    Copyright (C) 2011-2017 OpenFOAM Foundation
-------------------------------------------------------------------------------
Application
    buoyantBoussinesqSimpleFoam_new

Group
    grpHeatTransferSolvers

Description
    Steady-state solver for buoyant, turbulent flow of incompressible fluids.

    Identical to buoyantBoussinesqSimpleFoam, except that after every write
    step ALL physical boundary patches are re-written with their actual
    computed per-face values instead of a bare BC-type string. Stock OpenFOAM
    writes `type zeroGradient;` (T) and `type noSlip;` (U) with no numbers at
    all, so anything reading the raw files sees a hole exactly where the wall
    data should be.

    controlDict options (both optional):

        explicitBoundaryValues
        {
            mode                fixedValue;   // or preserveType
            writeFirstIteration false;        // force a write at iteration 1
        }

    `mode` is documented in writeBCFields.H. `writeFirstIteration` persists the
    very first SIMPLE iteration regardless of writeControl/writeInterval, which
    the ML dataset build needs as its t=1 input; it is OFF by default so this
    solver is a drop-in replacement for the stock one.

\*---------------------------------------------------------------------------*/

#include "fvCFD.H"
#include "singlePhaseTransportModel.H"
#include "turbulentTransportModel.H"
#include "radiationModel.H"
#include "fvOptions.H"
#include "simpleControl.H"
#include "pressureComponentReferences.H"

// Explicit boundary-value writer (must come after the OpenFOAM includes)
#include "writeBCFields.H"

// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

int main(int argc, char *argv[])
{
    argList::addNote
    (
        "Steady-state solver for buoyant, turbulent flow"
        " of incompressible fluids."
        " Writes actual boundary face values in result files."
    );

    #include "postProcess.H"

    #include "addCheckCaseOptions.H"
    #include "setRootCaseLists.H"
    #include "createTime.H"
    #include "createMesh.H"
    #include "createControl.H"
    #include "createFields.H"
    #include "initContinuityErrs.H"

    turbulence->validate();

    // * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

    Info<< "\nStarting time loop\n" << endl;

    // Optionally persist the FIRST SIMPLE iteration (see the write block
    // below). Off unless controlDict asks for it, so that this solver behaves
    // exactly like the stock one for ordinary runs.
    bool writeFirstIteration = false;
    {
        const dictionary* ebv =
            runTime.controlDict().findDict("explicitBoundaryValues");
        if (ebv)
        {
            ebv->readIfPresent("writeFirstIteration", writeFirstIteration);
        }
    }
    bool firstIter = true;

    while (simple.loop())
    {
        Info<< "Time = " << runTime.timeName() << nl << endl;

        // Pressure-velocity SIMPLE corrector
        {
            #include "UEqn.H"
            #include "TEqn.H"
            #include "pEqn.H"
        }

        laminarTransport.correct();
        turbulence->correct();

        // Persist results at the configured writeInterval. With
        // writeFirstIteration on, the very first iteration
        // (t = startTime + deltaT) is written as well, whatever
        // writeControl/writeInterval say: Time::writeNow() sets writeTime()
        // true and writes immediately. The normal cadence is untouched.
        if (firstIter && writeFirstIteration)
        {
            runTime.writeNow();
        }
        else
        {
            runTime.write();
        }
        firstIter = false;

        // Overwrite volField files with computed boundary values.
        // No-op unless this is a write step; after writeNow() writeTime() is
        // true, so the forced first step gets boundary values too.
        writeBCFields(mesh, runTime);

        runTime.printExecutionTime(Info);
    }

    // SIMPLE convergence calls Time::writeAndEnd after the last body write.
    // Restore explicit boundary values after that final stock serialisation.
    writeBCFields(mesh, runTime);

    Info<< "End\n" << endl;

    return 0;
}


// ************************************************************************* //
