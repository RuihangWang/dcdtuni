# Exact cone parent/base trace diagnostic

The real React load/export took 46.744 s, owned browser release 6.065 s, and full mesh API job 86.866 s (native 66.6758 s). The stock full-topology/full-geometry checker still rejected four checks. No solver seed is authorized.

The optional trace job retained the preceding writer-compatible geometry exactly: all eight ranks and all eight polyMesh files per rank have identical SHA256 and bytes. The optional source-stage and compact writer addressing identify written cells and bases without inferring ancestry from shape. The source snapshot is the alive local cell immediately before cone decomposition, not an octree or source-surface ancestor.

`exact-parent-samples-v2.json` includes the four certified skinny physical tetrahedra, two skew tetrahedra and the worst weight/ratio pair. Both rank-3 cells 498443 and 498446 map to original local parent 9885. Its current apex has a 0.533 mm minimum plane margin; its Chebyshev kernel radius is 23.611 mm. Maximizing the margin alone is not a quality fix: the independently checked Chebyshev candidate still fails determinant and weight checks.

`physical-skinny-child-unions-group5-v2.json` is a read-only enumeration of connected same-parent groups of at most five cells. Every candidate retains original boundary vertices, patch roles and zones, cancels only shared internal faces, and coalesces faces only with identical boundary/neighbour metadata. Directed edge closure, full vertex halfspaces, polygon planarity/convexity, original volume and available local OpenFOAM metrics are checked. Two groups for cell 604333 pass these available metrics; the other three sources have no such candidate in this enumeration. This is not a complete checker run, a changed mesh, or proof that larger groups cannot work.

The source project, exported input, native stdout, raw event stream, frozen experimental source patch, ELF identity, trace-array hashes and byte-parity receipt are preserved separately. Experimental cone decomposition and cleanup remain off by default.
