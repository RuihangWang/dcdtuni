# Native face-tet RED: shared collinear seam points

This OpenFOAM v2412 characterization reproduces the face-tet failure on a
closed, two-cell mesh without moving a surface or changing a quality threshold.
The cells are each `0.08 × 0.04 × 0.005 m` (`1.6e-5 m³`) and share face 0.
The RED case keeps two intermediate x coordinates on both long edges of that
face **and on every incident face**. Its shared face therefore has eight
vertices; the four-corner control has the same physical geometry and volume.

On 40901, choose an unused scratch directory and run:

```bash
cd /mnt/lv/home/litianyu/mycode/dcdtuni
source /mnt/lv/home/litianyu/miniconda3/etc/profile.d/conda.sh
conda activate dcdtuni
export FOAM_BASHRC=/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/etc/bashrc
export TMPDIR=$PWD/.codex-tmp
bash src/backend/tests/native/dccart_face_tet_collinear/Allrun \
  $PWD/.codex-tmp/dccart-face-tet-collinear-unique-name
```

`Allrun` uses one serial `checkMesh -allTopology -allGeometry` process per case,
one thread, `nice 10`, a 30-second limit, and no GPU. It refuses an existing
scratch directory. The expected v2412 result is seven bad face tets in the
collinear case, including the internal shared face 0, and `Face tets OK` in
the plain control. Both have closed cells, the same total volume `3.2e-5 m³`,
and two low-determinant cells because each has only one internal neighbour;
that shared background is unrelated to the face-tet difference. `checkMesh`
may exit zero even when it reports failed checks, so `Allrun` asserts the
terminal report and the written bad-face set.

This is a RED characterization, not a mesher repair or a solver-ready mesh.
OpenFOAM's fan triangulation cannot find a valid base point when each choice
produces a zero-area triangle from collinear edge vertices. In a real dcCart
mesh, those points can also belong to adjacent faces, physical patch seams,
and MPI matches. Deleting them from one face alone can open the cell or break
paired-face topology. A repair must preserve shared edges, source support,
patch/zone ownership and cross-rank matching, then pass strict `checkMesh`.
