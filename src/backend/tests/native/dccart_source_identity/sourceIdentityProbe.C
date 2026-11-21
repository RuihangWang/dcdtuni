#include "argList.H"
#include "Time.H"
#include "IOdictionary.H"
#include "OStringStream.H"
#include "dcCartSurfaces.H"
using namespace Foam;

static string geometrySignature(const dcCartSurfaces& surfaces)
{
    OStringStream out;
    out << surfaces.patchNames() << surfaces.patchTypes() << surfaces.nBaffleSheets();
    for (label sh=0; sh<surfaces.nBaffleSheets(); ++sh)
    {
        const auto& sheet=surfaces.baffleSheetInfo(sh);
        out << sheet.zone << sheet.origin << sheet.normal << sheet.bounds;
    }
    return out.str();
}

int main(int argc,char** argv)
{
    argList args(argc,argv);
    Time runTime(Time::controlDictName,args);
    IOdictionary input(IOobject("snappyHexMeshDict",runTime.system(),runTime,
                                IOobject::MUST_READ,IOobject::NO_WRITE));
    const dictionary& controls=input.subDict("castellatedMeshControls");
    dictionary geometry(input.subDict("geometry"));
    if (input.getOrDefault<bool>("duplicateGeometryEntry",false))
    {
        // Preserve two runtime geometry entries explicitly. A hand-written
        // duplicate key would already be merged by the OpenFOAM parser.
        const entry& first=*geometry.begin();
        geometry.IDLList<entry>::append(first.clone(geometry).ptr());
    }
    dcCartSurfaces surfaces(runTime,geometry,
                           controls.subDict("refinementSurfaces"));
    const string before=geometrySignature(surfaces);
    label count=0;
    if (input.found("dcdtuniSourceSurfaces",keyType::LITERAL))
    {
        count=surfaces.readSourceSurfaceIdentities(input.subDict("dcdtuniSourceSurfaces"));
    }
    if (geometrySignature(surfaces)!=before)
    {
        FatalErrorInFunction << "metadata changed geometry or patches" << exit(FatalError);
    }
    Info << "IDENTITIES " << count << nl << "GEOMETRY_UNCHANGED" << nl;
    forAll(surfaces.info(),si)
    {
        for (label ri=0; ri<surfaces.info()[si].nRegions; ++ri)
        {
            const auto* identity=surfaces.sourceIdentity(si,ri);
            Info << "IDENTITY " << si << ' ' << ri << ' ';
            if (!identity) { Info << "missing" << nl; continue; }
            Info << identity->sourceObjectUid << ' ' << identity->sourceFaceUid
                 << ' ' << identity->representation << nl;
        }
    }
    return 0;
}
