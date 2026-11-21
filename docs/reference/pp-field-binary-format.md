# `/api/pp/field/<name>` — the wire format, and the bug that comes from ignoring it

## The format

The response body is **a 16-byte binary header followed by the values**:

| offset | type      | meaning                        |
|--------|-----------|--------------------------------|
| 0      | float32   | field minimum (raw units)      |
| 4      | float32   | field maximum (raw units)      |
| 8      | uint32    | components per point (1 or 3)  |
| 12     | uint32    | point count                    |
| 16     | float32[] | `points x components` values   |

The same four numbers are repeated in an `X-Field-Info` HTTP header, as JSON.
Measured on `case/Base_leakage/log/2026-08-27-07-40-46_Base_leakage`, field `T`:

```
Content-Length: 658288
X-Field-Info: {"min": 288.1499938964844, "max": 310.8757629394531,
               "nComponents": 1, "nPoints": 164568}

binary header: min=288.1500 max=310.8758 nComp=1 nPoints=164568
values after the header: 164568        <- exactly /api/pp/mesh's point count
```

The legacy editor parses it in `window._ppParseFieldBinary` (in `STL_generate.html`)
and React in `decodeField` (`src/frontend/react/src/postprocess/index.ts`).

## The failure it causes when skipped

Reading the body as one flat `Float32Array` — no header skip — is nearly
invisible, and it was shipped in this port before being caught:

- The array comes back **four values too long** (164572 for 164568 points).
- Every point takes **the value of the point four positions later**. The picture
  keeps its structure; hot regions stay roughly hot. Nothing looks broken.
- The first four "values" are the header reinterpreted as floats:
  `288.15, 310.88, 1.4e-45, 2.3e-40`. An auto-scaled colour range therefore runs
  from ~0 to 310 instead of 288 to 311, so the entire room — an 18 K spread —
  compresses into the top few percent of the ramp and renders as one flat warm
  colour.

The second symptom is what makes this worth writing down: a wrong colour SCALE
looks like a rendering or lighting problem, not like a decode problem, and it
sends you to the material.

## What catches it

`buildSurface` requires the field length to equal the mesh point count
**exactly**, in both directions (`src/frontend/react/src/postprocess/ppRenderer.ts`). The
surplus of four is what surfaced the bug. An earlier version of that check
allowed a longer field and used its head — which "fixed" the error message while
leaving every value shifted by four. Do not relax it again.

Regression tests: `src/frontend/react/test/postprocess.test.ts` (field payload decode) and
`src/frontend/react/test/ppRenderer.test.ts` (the exact-length check).

Since 2026-09 the field endpoint, like the other post-processing data routes,
also requires the `X-PP-Revision` header returned by `POST /api/pp/init`
(missing: HTTP 428, stale: 409); the payload format above is unchanged.
