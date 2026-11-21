/*---------------------------------------------------------------------------*\
Application
    writeExplicitBC

Description
    Re-write a RECONSTRUCTED time directory so every physical boundary patch
    carries its actual per-face values.

    Why this exists as a separate utility, rather than being finished inside the
    solver: buoyantBoussinesqSimpleFoam_new already writes explicit boundary
    values, but on a parallel run it writes them into `processorN/<time>/<field>`.
    `reconstructPar` then merges the processor fields into `<time>/<field>` by
    re-serialising them the stock way — which puts `type zeroGradient;` with no
    values back into the one file everybody actually reads. Measured on a real
    100-iteration run of case/Base_leakage: processor0/100/T had 56 of 58
    patches written nonuniform, while the reconstructed 100/T had 41 of 53
    patches carrying nothing but a type.

    So the explicit write has to happen AFTER reconstruction. Run this once the
    reconstruct step is done:

        writeExplicitBC -latestTime
        writeExplicitBC -time 100

    Mode comes from controlDict, same as in the solver:

        explicitBoundaryValues { mode preserveType; }   // or fixedValue

    Constraint and coupled patches are left untouched — their type IS the data.

\*---------------------------------------------------------------------------*/

#include "fvCFD.H"
#include "timeSelector.H"
#include "IOobjectList.H"
#include "writeBCFields.H"

// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

// Read every AUTO_WRITE-able volField of one type present in this time
// directory, then re-write it with explicit boundaries. The objects are held in
// a PtrList so they stay alive until after the write.
template<class FieldType>
Foam::label readAndWrite
(
    const Foam::fvMesh& mesh,
    const Foam::IOobjectList& objects,
    const Foam::bcWriteMode mode
)
{
    using namespace Foam;

    IOobjectList fieldObjects(objects.lookupClass(FieldType::typeName));
    label n = 0;

    // sortedNames so the output order does not depend on directory iteration
    for (const word& name : fieldObjects.sortedNames())
    {
        IOobject& io = *fieldObjects[name];
        io.readOpt(IOobject::MUST_READ);
        io.writeOpt(IOobject::NO_WRITE);

        FieldType field(io, mesh);
        writeBCField(field, mode);
        Info<< "    " << name;
        ++n;
    }
    return n;
}


int main(int argc, char *argv[])
{
    Foam::argList::addNote
    (
        "Re-write a reconstructed time directory with explicit boundary values,"
        " undoing what reconstructPar's stock serialisation drops."
    );
    Foam::timeSelector::addOptions();

    #include "setRootCase.H"
    #include "createTime.H"

    Foam::instantList timeDirs = Foam::timeSelector::select0(runTime, args);

    #include "createMesh.H"

    const Foam::bcWriteMode mode = Foam::readBCWriteMode(runTime);

    forAll(timeDirs, timei)
    {
        runTime.setTime(timeDirs[timei], timei);

        // Time 0 is the case setup, not a result: rewriting it would replace a
        // hand-written or generated 0/ directory with solver output.
        if (runTime.timeName() == "0")
        {
            Info<< "Skipping time 0 (case setup, not a result)" << endl;
            continue;
        }

        mesh.readUpdate();

        Foam::IOobjectList objects(mesh, runTime.timeName());

        Info<< "Time = " << runTime.timeName() << nl
            << "writeExplicitBC ("
            << (mode == Foam::bcWriteMode::fixedValue ? "fixedValue" : "preserveType")
            << "):";

        Foam::label n = 0;
        n += readAndWrite<Foam::volScalarField>(mesh, objects, mode);
        n += readAndWrite<Foam::volVectorField>(mesh, objects, mode);
        n += readAndWrite<Foam::volSymmTensorField>(mesh, objects, mode);
        n += readAndWrite<Foam::volTensorField>(mesh, objects, mode);

        Info<< "  (" << n << " fields)" << endl;
    }

    Info<< "End" << nl << endl;
    return 0;
}

// ************************************************************************* //
