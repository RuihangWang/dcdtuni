#include "fvCFD.H"
#include "mixedFvPatchFields.H"
int main(int argc, char *argv[])
{
    timeSelector::addOptions();
    #include "setRootCase.H"
    #include "createTime.H"
    instantList ts = timeSelector::select0(runTime, args);
    #include "createMesh.H"
    runTime.setTime(ts.last(), 0);
    {

        const fvMesh& m = mesh;
        volScalarField T(IOobject("T", m.time().timeName(), m, IOobject::MUST_READ), m);
        volScalarField alphat(IOobject("alphat", m.time().timeName(), m, IOobject::MUST_READ), m);
        surfaceScalarField phi(IOobject("phi", m.time().timeName(), m, IOobject::MUST_READ), m);
        IOdictionary tp(IOobject("transportProperties", m.time().constant(), m, IOobject::MUST_READ));
        const scalar nu = dimensionedScalar("nu", dimViscosity, tp).value();
        const scalar Pr = dimensionedScalar("Pr", dimless, tp).value();
        volScalarField alphaEff("alphaEff", alphat + dimensionedScalar("a", alphat.dimensions(), nu/Pr));
        const scalar rcp = 1.19*1005.0;
        fvScalarMatrix D(fvm::div(phi, T, "div(phi,T)"));
        fvScalarMatrix L(fvm::laplacian(alphaEff, T, "laplacian(alphaEff,T)"));
        fvScalarMatrix TE(D - L);
        // residual(): b - A*T (per cell, extensive, incl. boundary coefficients)
        scalarField rTE(TE.residual()), rD(D.residual()), rL(L.residual());
        Info<< "SUM residual(TEqn)  W = " << rcp*gSum(rTE) << nl
            << "SUM residual(div)   W = " << rcp*gSum(rD) << nl
            << "SUM residual(lapl)  W = " << rcp*gSum(rL) << nl
            << "SUM |residual(TEqn)| W = " << rcp*gSum(mag(rTE)) << endl;
        volScalarField R(IOobject("TEqnResidualW", m.time().timeName(), m, IOobject::NO_READ, IOobject::AUTO_WRITE), m, dimensionedScalar("z", dimless, 0));
        R.primitiveFieldRef() = rcp*rTE;
        R.write();
        // explicit laplacian (fvc) with the same scheme, as a cross-check

        {
            surfaceScalarField gf("gf", fvc::interpolate(alphaEff)*m.magSf()*fvc::snGrad(T));
            forAll(m.boundary(), pi)
            {
                scalar s = rcp*sum(gf.boundaryField()[pi]);
                if (mag(s) > 1e-3) Pout<< "PATCH " << m.boundary()[pi].name() << " " << m.boundary()[pi].type() << " diffusive-in W = " << s << endl;
            }
        }

        forAll(m.boundary(), pi)
        {
            const fvPatchScalarField& tp = T.boundaryField()[pi];
            if (tp.type() != "inletOutlet" || tp.size() == 0) continue;
            const mixedFvPatchScalarField& mp = refCast<const mixedFvPatchScalarField>(tp);
            scalarField dT(tp - tp.patchInternalField());
            Pout<< "IO " << m.boundary()[pi].name() << " n=" << tp.size()
                << " vf min/max " << min(mp.valueFraction()) << " " << max(mp.valueFraction())
                << " refValue " << average(mp.refValue())
                << " Tb-Tp min/mean/max " << min(dT) << " " << average(dT) << " " << max(dT)
                << " snGrad mean " << average(tp.snGrad()) << endl;

            {
                const scalarField& vf = mp.valueFraction();
                const scalarField& ph = phi.boundaryField()[pi];
                const scalarField& A = m.magSf().boundaryField()[pi];
                const scalarField dc(m.boundary()[pi].deltaCoeffs());
                const scalarField ae(alphaEff.boundaryField()[pi]);
                scalar q1=0, a1=0, p1=0, n1=0, dmax=0; scalar pmin=GREAT, pmax=-GREAT;
                forAll(vf, f) if (vf[f] > 0.5) { n1++; a1+=A[f]; p1+=ph[f]; pmin=min(pmin,ph[f]); pmax=max(pmax,ph[f]); dmax=max(dmax,dc[f]); q1 += 1.19*1005*ae[f]*A[f]*dc[f]*dT[f]; }
                Pout<< "VF1 " << m.boundary()[pi].name() << " faces=" << n1 << " area=" << a1 << " of " << sum(A) << " phi sum/min/max " << p1 << " " << pmin << " " << pmax
                    << " max deltaCoeff " << dmax << " (median-ish all " << average(dc) << ") diffusive W " << q1 << endl;
            }
        }
        volScalarField lapc(fvc::laplacian(alphaEff, T, "laplacian(alphaEff,T)"));
        volScalarField divc(fvc::div(phi, T, "div(phi,T)"));
        Info<< "SUM V*fvc::div(phi,T)            W = " << rcp*gSum(divc.primitiveField()*m.V()) << nl
            << "SUM V*fvc::laplacian(alphaEff,T) W = " << rcp*gSum(lapc.primitiveField()*m.V()) << endl;
    
    }
    return 0;
}
