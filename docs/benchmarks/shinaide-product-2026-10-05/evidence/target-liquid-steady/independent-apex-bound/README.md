Independent review of the fixed-triangle apex bound

This is a read-only installed-source and pure mathematical review. No native program, API, browser or pytest was executed. The original mesh inputs and quality thresholds were not changed. Numerical searches are supplementary checks; the all-apex conclusion follows from the proof below.

The installed 3D checker computes D = |det(Σ s_i s_i^T / avgArea²)| / 8, with avgArea = S/n. primitiveMeshTools.C lines 829–931 contains the formula; polyMeshCheck.C lines 416–449 invokes it using syncTools::getInternalOrCoupledFaces and warnDet = 0.001. syncTools.C lines 171–186 excludes noncoupled boundary faces. polyMeshCheck.C lines 736–748 supplies geometricD. The original source observations identify the actual mesh as three dimensional.

For a nondegenerate tetrahedron with one fixed noncoupled physical triangular base, the other three faces are the selected side faces. Let A > 0 be the base area, P the perimeter, h > 0 the perpendicular distance of the apex from its base plane, and S the sum of the three side areas. Let B have the three oriented area vectors as rows. In a base-plane coordinate frame, |det B| = A²h²/2. Consequently

D = det(B)^2 / (8(S/3)^6) = 729 A^4 h^4 / (32 S^6).

An apex projection outside the triangle does not invalidate this equation. It also does not invalidate either lower bound. The three side area vectors sum, up to sign, to the base vector, so S >= A. For edge i of length L_i and signed distance d_i from the projected apex to its supporting line, its side area is L_i sqrt(h²+d_i²)/2. Hence S >= h P/2 for every projection, including projections outside the base.

Using S >= max(A,hP/2), the bound as a function of h increases up to h = 2A/P and decreases thereafter. This proves

D <= (729/2) A²/P^4.

P >= 2L for the longest edge L gives the looser bound

D <= 729 A²/(32 L^4).

The diagnostic tool certifies the latter using exact Fraction arithmetic on the written binary double base coordinates; it does not certify the rounded perimeter calculation. All four independently recomputed exact fractions match the committed diagnostic and are strictly below 0.001. Independent raw owner/neighbour/boundary verification confirmed exactly three internal side faces, one physical base and zero coupled faces for these four actual tetrahedra.

A stronger mathematical cross-check is available without changing the tool. Consistently oriented edge distances satisfy Σ L_i d_i = 2A even for an outside projection. The triangle inequality in the two-dimensional (h,d_i) plane yields S >= sqrt(A²+(hP/2)²). Set r = 2A/P and x = h/r. Then D <= (729/2)(A²/P^4) x^4/(1+x²)^3. This expression reaches its maximum 54 A²/P^4 at x² = 2. Equality is attainable with projected apex at the incenter, so it is the true maximum for the fixed triangular base and these three selected sides. The four raw triangles give maxima approximately 4.04677e-5, 1.14490e-6, 1.24691e-4 and 1.24691e-4. The written math-proof.json contains the explicit incenter/apex witnesses.

The independent search used 12,000 nondegenerate tetrahedra across acute/obtuse/slender base configurations and positive/negative heights; 11,949 apex projections were outside. Neither lower-bound nor determinant upper-bound violations occurred. Sixteen named cases also verified the side-vector triple-product identity with exact Fraction arithmetic. These checks support implementation arithmetic; they are not the proof of the universal inequality.

The exact THREE-side formula is tied to its selection branch. A nondegenerate tetrahedron with base (0,0,0), (1,0,0), (0,1,0) and apex (.2,.2,.4) gives D = .09039595701524346 when only the three sides are selected, and D = .12973834946121557 when the base is selected as well. This is a counterexample to reusing the three-side equality under four-face selection, not a counterexample to the upper bound. The actual four target cells all use the three-side branch.

This result excludes fixing those four written tetrahedra solely by changing the apex while preserving each exact physical triangle and single-tetrahedron topology. It does not establish that the full target mesh cannot be repaired. Different cell topology, valid tessellation and other repairs preserving the physical surface remain outside this proof. It is an exact-real geometric inequality, not a formal guarantee for arbitrary IEEE-754 overflow/underflow inputs or a claim of native parent ancestry.
