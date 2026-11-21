# Compatible roof components and a protected-border obstruction

Compatible point proposals alone do not prove that their union's complete point star remains planar. This independent NumPy audit uses the same exact54-cell buffered fixture and checks all54 cells after each in-memory trial. No C++, native mesh, executable or source STL is changed; no native checker or solver is run. The trials use unit-metre boxes and normal roof amplitude0.0005m, not an assertion about production-case component eligibility.

Two same-plane, same-normal-extent2×2 roof packets share an edge midpoint whose displacement is exactly(0.00025,0,0)m in both packets. At the common midpoint of orthogonalX/Y transitions, the proposals are(0.00025,0,0)m and(0,0.00025,0)m, differing by0.000353553391m. They must be rejected as incompatible; averaging does not retain either packet's planar construction. Unequal normal extents also produce different nonzero proposals at a shared edge midpoint.

| Pure54-cell trial | Nonplanar faces | Cells containing nonplanar polygons | Maximum face deviation |
| --- | ---: | ---: | ---: |
| All nine ideal compatible packets |0|0|2.22e-16m|
| Only central packet, entire star checked |8|12|0.00006250m|
| All nine packets, outer-border points fixed |20|28|0.00012500m|

The all-nine ideal arrangement has no strict face-centre flags, but it moves border vertices and therefore is not eligible merely because the mathematics passes. An isolated interior packet warps neighboring fine faces. Clamping the ideal arrangement's outer border creates20 warped faces while the strict face-centre test still flags zero cells. This last result proves why that criterion cannot replace explicit face planarity.

For a regular rectangular projected fine-quad grid, a normal displacement heightd preserves each quadrilateral plane only ifd00+d11−d10−d01=0. Summing these mixed differences yieldsd(i,j)=d(i,0)+d(0,j)−d(0,0)=a(i)+b(j). If the entire outer border is fixed at zero, these values are zero everywhere. A nonzero roof confined inside a fully fixed rectangular border cannot retain all original quad planes. It must be rejected under the current protection/topology rules, or a separately proved topological construction would be needed.

The integer mixed-difference and fixed-border matrices for2×2,4×4 and6×6 fine quads have rank9,25 and49 respectively, equal to their vertex counts: nullity zero. In the explicit clamped7×7-point example the maximum mixed difference is0.0005m. These small numerical rank checks support the preceding exact telescoping proof; the proof does not depend on SVD precision.

Six focused tests pass: shared-point agreement, orthogonal-normal conflict, the three frozen-border ranks and whole-star warp counterexamples. The JSON preserves the complete height grids, affected face coordinates, all cell summaries and source hashes. This does not prove that every real Tri or Liquid component has a complete fixed rectangular border. Real exported proposals, corner constraints, physical/coupled/zone protections, omitted neighboring packets and whole-star topology must be examined independently.
