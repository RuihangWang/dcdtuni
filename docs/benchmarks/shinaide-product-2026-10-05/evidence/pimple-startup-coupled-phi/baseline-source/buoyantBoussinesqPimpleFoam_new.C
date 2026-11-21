/*---------------------------------------------------------------------------*\
  =========                 |
  \\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox
   \\    /   O peration     |
    \\  /    A nd           | www.openfoam.com
     \\/     M anipulation  |
-------------------------------------------------------------------------------
    Copyright (C) 2011-2017 OpenFOAM Foundation
    Copyright (C) 2021 OpenCFD Ltd.
-------------------------------------------------------------------------------
License
    This file is part of OpenFOAM.

    OpenFOAM is free software: you can redistribute it and/or modify it
    under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    OpenFOAM is distributed in the hope that it will be useful, but WITHOUT
    ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
    FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
    for more details.

    You should have received a copy of the GNU General Public License
    along with OpenFOAM.  If not, see <http://www.gnu.org/licenses/>.

Application
    buoyantBoussinesqPimpleFoam_new

Group
    grpHeatTransferSolvers

Description
    Transient solver for buoyant, turbulent flow of incompressible fluids,
    with component pressure references and explicit boundary values.
    This repository build supports static meshes only.

    Uses the Boussinesq approximation:
    \f[
        rho_{k} = 1 - beta(T - T_{ref})
    \f]

    where:
        \f$ rho_{k} \f$ = the effective (driving) kinematic density
        beta = thermal expansion coefficient [1/K]
        T = temperature [K]
        \f$ T_{ref} \f$ = reference temperature [K]

    Valid when:
    \f[
        \frac{beta(T - T_{ref})}{rho_{ref}} << 1
    \f]

\*---------------------------------------------------------------------------*/

// Source basis: official OpenFOAM-v2412 release, file hashes in
// docs/benchmarks/pressure-component-gauge-2026-10-05/upstream-v2412/source-receipt.json.
// UEqn.H and TEqn.H retain the upstream equations byte-for-byte.
#include "fvCFD.H"
#include "dynamicFvMesh.H"
#include "singlePhaseTransportModel.H"
#include "turbulentTransportModel.H"
#include "radiationModel.H"
#include "CorrectPhi.H"
#include "fvOptions.H"
#include "pimpleControl.H"
#include "staticFvMesh.H"
#include "pressureComponentReferences.H"
#include "writeBCFields.H"
#include "transientTimeBoundaries.H"

// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

int main(int argc, char *argv[])
{
    argList::addNote
    (
        "Transient solver for buoyant, turbulent flow"
        " of incompressible fluids on static meshes.\n"
        "Uses the Boussinesq approximation."
    );

    #include "postProcess.H"

    #include "addCheckCaseOptions.H"
    #include "setRootCaseLists.H"
    #include "createTime.H"
    // Reject non-static declarations before their motion/topology constructors.
    {
        IOdictionary dynamicSettings
        (
            IOobject("dynamicMeshDict", runTime.constant(), runTime,
                IOobject::READ_IF_PRESENT, IOobject::NO_WRITE)
        );
        if (dynamicSettings.getOrDefault<word>
            ("dynamicFvMesh", "staticFvMesh") != "staticFvMesh")
        {
            FatalErrorInFunction
                << "buoyantBoussinesqPimpleFoam_new supports static meshes only; "
                << "dynamic motion/topology requires rebuilding the pressure graph."
                << exit(FatalError);
        }
    }
    #include "createDynamicFvMesh.H"
    if (!isA<staticFvMesh>(mesh))
    {
        FatalErrorInFunction
            << "buoyantBoussinesqPimpleFoam_new supports static meshes only."
            << exit(FatalError);
    }
    #include "createDyMControls.H"
    #include "createFields.H"
    #include "createUfIfPresent.H"
    #include "CourantNo.H"
    #include "setInitialDeltaT.H"
    #include "initContinuityErrs.H"

    turbulence->validate();

    // * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

    Info<< "\nStarting time loop\n" << endl;

    while (dcRunTransient(runTime))
    {
        #include "readDyMControls.H"
        #include "CourantNo.H"
        #include "setDeltaT.H"
        dcLimitTransientDeltaT(runTime);

        ++runTime;

        Info<< "Time = " << runTime.timeName() << nl << endl;

        // --- Pressure-velocity PIMPLE corrector loop
        while (pimple.loop())
        {
            if (pimple.firstIter() || moveMeshOuterCorrectors)
            {
                // Do any mesh changes
                mesh.controlledUpdate();

                if (mesh.changing() || mesh.topoChanging())
                {
                    FatalErrorInFunction
                        << "Static pressure-component graph cannot be reused "
                        << "after mesh motion/topology changes."
                        << exit(FatalError);
                }
            }

            #include "UEqn.H"
            #include "TEqn.H"

            // --- Pressure corrector loop
            while (pimple.correct())
            {
                #include "pEqn.H"
            }

            if (pimple.turbCorr())
            {
                laminarTransport.correct();
                turbulence->correct();
            }
        }

        // Persist the genuine final solve even when endTime is off the
        // configured writeInterval grid.
        if (dcAtTransientEnd(runTime))
        {
            runTime.writeNow();
        }
        else
        {
            runTime.write();
        }
        writeBCFields(mesh, runTime);

        runTime.printExecutionTime(Info);
    }

    Info<< "End\n" << endl;

    return 0;
}


// ************************************************************************* //
