inline void faceAreaCentreOF
(
    const std::vector<Foam::point>& pts,
    const std::vector<Foam::label>& poly,
    Foam::vector& A,
    Foam::point& C
)
{
    using namespace Foam;
    A = vector::zero;
    C = point::zero;
    const label k = label(poly.size());
    if (k < 3)
    {
        for (label v : poly) { C += pts[v]; }
        if (k) { C /= scalar(k); }
        return;
    }
    if (k == 3)
    {
        C = (pts[poly[0]] + pts[poly[1]] + pts[poly[2]]) / 3.0;
        A = 0.5 * ((pts[poly[1]] - pts[poly[0]]) ^ (pts[poly[2]] - pts[poly[0]]));
        return;
    }
    point fc = point::zero;
    for (label i = 0; i < k; ++i) { fc += pts[poly[i]]; }
    fc /= scalar(k);
    vector sumN = vector::zero;
    vector sumAc = vector::zero;
    scalar sumA = 0;
    for (label i = 0; i < k; ++i)
    {
        const point& p = pts[poly[i]];
        const point& q = pts[poly[(i + 1) % k]];
        const vector n = (q - p) ^ (fc - p);
        const scalar a = mag(n);
        sumN += n;
        sumA += a;
        sumAc += a * (p + q + fc);
    }
    if (sumA < ROOTVSMALL)
    {
        C = fc;
        return;
    }
    // Match primitiveMeshTools operation order, including rounding at the
    // strict skewness limit. Algebraically equivalent expressions can fall
    // on opposite sides of checkMesh's boundary threshold.
    C = (1.0 / 3.0) * sumAc / sumA;
    A = 0.5 * sumN;
}

