/*---------------------------------------------------------------------------*\
Description
    writeBCFields.H — re-write result fields so that every physical boundary
    patch carries its ACTUAL per-face values.

    OpenFOAM writes a boundary patch by asking the patch field to serialise
    itself, and several very common types serialise to nothing but their name:

        ACU_1_Front  { type zeroGradient; }      // volScalarField T
        ACU_1_Front  { type noSlip; }            // volVectorField U

    The values are perfectly well defined inside the solver — they are just not
    in the file, and every downstream consumer is expected to re-derive them.
    Anything that reads the raw files (an ML pipeline, a custom post-processor,
    a diff between two runs) therefore sees a hole exactly where the wall data
    should be.

    After runTime.write() has produced the time directory, this re-writes each
    AUTO_WRITE volField with the boundary values spelled out.

Modes
    fixedValue    (default) every physical patch becomes
                      type fixedValue;  value nonuniform List<...> (...);
                  Fully explicit and trivially readable. NOT restartable: a
                  fixedFluxPressure or flowRateInletVelocity that becomes a
                  fixedValue no longer reproduces the original run.

    preserveType  keep the real BC type and all of its own entries, drop its
                  `value` entry if it had one, and append the explicit per-face
                  values. Just as complete for a reader, and the case still
                  restarts, because a zeroGradient/noSlip patch simply ignores
                  a stray `value` on read.

    Select in controlDict:

        explicitBoundaryValues
        {
            mode        preserveType;   // or fixedValue (default)
        }

    Constraint and coupled patches (processor, cyclic, empty, wedge, symmetry,
    symmetryPlane) are always written untouched: their type IS the data, and
    overwriting it would break mesh coupling.

Usage
    Call AFTER runTime.write() inside the time loop:

        runTime.write();
        writeBCFields(mesh, runTime);

Note
    These files are always written in ASCII regardless of the case's
    writeFormat. Each OpenFOAM file records its own format in its header, so a
    binary-format case reads them back without complaint.

\*---------------------------------------------------------------------------*/

#ifndef writeBCFields_H
#define writeBCFields_H

#include "volFields.H"
#include "OFstream.H"
#include "OStringStream.H"
#include "IStringStream.H"
#include "dictionary.H"

// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

namespace Foam
{

//- How a physical boundary patch is re-written
enum class bcWriteMode
{
    fixedValue,     //!< replace the type with fixedValue
    preserveType    //!< keep the type, append an explicit value entry
};


//- Read the mode from controlDict once per run.
inline bcWriteMode readBCWriteMode(const Time& runTime)
{
    word modeName("fixedValue");
    const dictionary* dictPtr =
        runTime.controlDict().findDict("explicitBoundaryValues");
    if (dictPtr)
    {
        dictPtr->readIfPresent("mode", modeName);
    }

    if (modeName == "preserveType")
    {
        return bcWriteMode::preserveType;
    }
    if (modeName != "fixedValue")
    {
        WarningInFunction
            << "Unknown explicitBoundaryValues/mode '" << modeName
            << "'; using fixedValue" << endl;
    }
    return bcWriteMode::fixedValue;
}


//- Write "value nonuniform List<Type> N ( ... );" with EVERY face listed.
//  Field<Type>::writeEntry() collapses an all-equal field to "uniform X",
//  which hides the per-face data this whole header exists to expose, so the
//  list is emitted by hand and is always nonuniform.
template<class Type>
void writeExplicitValueEntry(Ostream& os, const Field<Type>& faceVals)
{
    os  << "        value           nonuniform List<"
        << pTraits<Type>::typeName << ">" << nl
        << faceVals.size() << nl << '(' << nl;
    forAll(faceVals, i)
    {
        os << faceVals[i] << nl;
    }
    os << ')' << nl << ';' << nl;
}


//- Re-write one volField with explicit boundary values.
template<class Type>
void writeBCField
(
    const GeometricField<Type, fvPatchField, volMesh>& field,
    const bcWriteMode mode
)
{
    // correctBoundaryConditions() is deliberately NOT called here. By the time
    // this runs (after turbulence->correct() and the full SIMPLE loop) every
    // patch field's Field<Type> base already holds the current face values.
    // Re-evaluating would ask wall functions such as epsilonWallFunction for
    // temporaries (kEpsilon:G) that only live during turbulence correction and
    // are already gone.

    OFstream os(field.time().timePath()/field.name());

    field.writeHeader(os);
    field.internalField().writeData(os, "internalField");
    os << nl;

    os << "boundaryField" << nl << '{' << nl;

    forAll(field.boundaryField(), patchi)
    {
        const fvPatch& patch = field.mesh().boundary()[patchi];
        const fvPatchField<Type>& pf = field.boundaryField()[patchi];

        os << "    " << patch.name() << nl << "    {" << nl;

        // A constraint / coupled patch's type carries mesh topology, not data.
        const bool isPhysical =
            !pf.coupled()
         && patch.type() != "empty"
         && patch.type() != "wedge"
         && patch.type() != "symmetry"
         && patch.type() != "symmetryPlane";

        if (!isPhysical)
        {
            os << incrIndent << incrIndent;
            pf.write(os);
            os << decrIndent << decrIndent;
        }
        else
        {
            const Field<Type>& faceVals = static_cast<const Field<Type>&>(pf);

            if (mode == bcWriteMode::fixedValue)
            {
                os  << "        type            fixedValue;"
                    << "  // original: " << pf.type() << nl;
            }
            else
            {
                // Round-trip the patch through a dictionary so its own entries
                // survive verbatim while any `value` it wrote is dropped — two
                // `value` keys in one dictionary is a read error.
                OStringStream oss;
                pf.write(oss);
                dictionary patchDict(IStringStream(oss.str())());
                patchDict.remove("value");
                os << incrIndent << incrIndent;
                patchDict.write(os, false);
                os << decrIndent << decrIndent;
            }

            writeExplicitValueEntry<Type>(os, faceVals);
        }

        os << "    }" << nl;
    }

    os << '}' << nl << nl;
    os << "// ************************************************************************* //"
       << nl;
}


//- Re-write every AUTO_WRITE volField registered on the mesh.
//  Call AFTER runTime.write().
template<class FieldType>
label writeBCFieldsOfType(const fvMesh& mesh, const bcWriteMode mode)
{
    label n = 0;
    for (const word& name : mesh.sortedNames<FieldType>())
    {
        const FieldType& f = mesh.lookupObject<FieldType>(name);
        if (f.writeOpt() != IOobject::AUTO_WRITE)
        {
            continue;
        }
        writeBCField(f, mode);
        Info<< "    " << name;
        ++n;
    }
    return n;
}


inline void writeBCFields(const fvMesh& mesh, const Time& runTime)
{
    if (!runTime.writeTime())
    {
        return;
    }

    static const bcWriteMode mode = readBCWriteMode(runTime);

    Info<< "writeBCFields ("
        << (mode == bcWriteMode::fixedValue ? "fixedValue" : "preserveType")
        << "):";

    label n = 0;
    n += writeBCFieldsOfType<volScalarField>(mesh, mode);
    n += writeBCFieldsOfType<volVectorField>(mesh, mode);
    n += writeBCFieldsOfType<volSymmTensorField>(mesh, mode);
    n += writeBCFieldsOfType<volTensorField>(mesh, mode);

    Info<< "  (" << n << " fields)" << endl;
}


} // End namespace Foam

#endif

// ************************************************************************* //
