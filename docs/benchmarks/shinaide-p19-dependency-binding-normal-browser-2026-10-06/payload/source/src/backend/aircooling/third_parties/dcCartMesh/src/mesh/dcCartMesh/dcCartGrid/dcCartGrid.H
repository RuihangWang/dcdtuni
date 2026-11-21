/*---------------------------------------------------------------------------*\
  dcCartGrid — the graded Cartesian background grid, and the integer index
  space the octree lives in.

  The grid is a tensor product of three sorted breakpoint arrays. Breakpoints
  sit ON object faces (that is what makes a data-centre model mesh with no
  stair-stepping at all on axis-aligned boxes), so spacing is deliberately
  NON-uniform. Everything downstream works in integer indices; this class is
  the only place that knows about physical coordinates.

  Index space
  -----------
  A background cell is (I,J,K) with 0 <= I < nx(). A cell at octree level L
  is addressed by (i,j,k) in the level-L space, which is the background space
  refined 2^L times per axis:

      I = i >> L                       background cell
      li = i & ((1 << L) - 1)          sub-index inside it
      x  = x_[I] + (x_[I+1] - x_[I]) * li / 2^L

  So refinement subdivides each background interval uniformly, and every cell
  face still lies on a plane of the tensor grid. A level-L index is exactly a
  level-(L+1) index halved, which is what lets the octree do parent/child
  arithmetic with shifts.

  A single packed uint64 key (21 bits per axis) identifies a cell within one
  level, which is what the octree hashes on.

\*---------------------------------------------------------------------------*/

#ifndef dcCartGrid_H
#define dcCartGrid_H

#include "scalarField.H"
#include "labelVector.H"
#include "boundBox.H"
#include "point.H"
#include "dictionary.H"

namespace Foam
{

//- Bits per axis in a packed cell key. 21 bits caps one axis at 2,097,152
//  cells at the finest level, far beyond any mesh that fits in memory.
static constexpr unsigned dcCartKeyBits = 21;
static constexpr uint64_t dcCartKeyMask = (uint64_t(1) << dcCartKeyBits) - 1;

//- Pack a per-level (i,j,k) index into one key.
inline uint64_t dcCartPack(label i, label j, label k)
{
    return (uint64_t(i) << (2 * dcCartKeyBits))
         | (uint64_t(j) << dcCartKeyBits)
         | uint64_t(k);
}

//- Inverse of dcCartPack.
inline labelVector dcCartUnpack(uint64_t key)
{
    return labelVector
    (
        label((key >> (2 * dcCartKeyBits)) & dcCartKeyMask),
        label((key >> dcCartKeyBits) & dcCartKeyMask),
        label(key & dcCartKeyMask)
    );
}


class dcCartGrid
{
    // Private data

        //- Breakpoints per axis, strictly increasing. Sizes n+1.
        scalarField coords_[3];


    // Private Member Functions

        //- Sort, de-duplicate and validate one breakpoint array.
        static scalarField cleaned(const scalarField& raw, const word& axis);


public:

    // Constructors

        //- From three breakpoint arrays (sorted and de-duplicated on the way in)
        dcCartGrid(const scalarField& x, const scalarField& y, const scalarField& z);

        //- From a dictionary holding `x`, `y`, `z` breakpoint lists
        explicit dcCartGrid(const dictionary& dict);

        //- Uniform grid over a bounding box with the given cell counts
        dcCartGrid(const boundBox& bb, const labelVector& n);


    // Member Functions

        //- Background cell count along an axis
        inline label n(direction cmpt) const { return coords_[cmpt].size() - 1; }
        inline label nx() const { return n(0); }
        inline label ny() const { return n(1); }
        inline label nz() const { return n(2); }

        //- Background cell count as a vector
        inline labelVector nCellsVector() const
        {
            return labelVector(nx(), ny(), nz());
        }

        //- Total background cells
        inline label nBackgroundCells() const { return nx() * ny() * nz(); }

        //- Breakpoint array for an axis
        inline const scalarField& breakpoints(direction cmpt) const
        {
            return coords_[cmpt];
        }

        //- Number of level-`level` cells along an axis
        inline label nAtLevel(direction cmpt, label level) const
        {
            return n(cmpt) << level;
        }

        //- Physical coordinate of grid plane `i` at `level` along `cmpt`.
        //  Valid for 0 <= i <= nAtLevel(cmpt, level).
        scalar planeCoord(direction cmpt, label level, label i) const;

        //- Bounding box of the level-`level` cell at index `idx`
        boundBox cellBox(label level, const labelVector& idx) const;

        //- Centre of the level-`level` cell at index `idx`
        point cellCentre(label level, const labelVector& idx) const;

        //- Whole-domain bounding box
        boundBox bounds() const;

        //- Index of the level-`level` cell containing `pt`, clamped into range.
        //  Returns false when `pt` lies outside the domain.
        bool findCell(label level, const point& pt, labelVector& idx) const;

        //- Largest level whose packed keys still fit in dcCartKeyBits per axis
        label maxRepresentableLevel() const;

        //- Smallest cell edge length at `level` (over all three axes)
        scalar minCellSize(label level) const;

        //- Largest cell edge length at `level`
        scalar maxCellSize(label level) const;

        //- Median background spacing over all three axes. The TYPICAL cell
        //  size of a graded grid: breakpoints sit on object faces, so the
        //  minimum is a fraction of a millimetre wherever two objects nearly
        //  touch, and any tolerance scaled from it would snap nothing.
        scalar medianSpacing() const;

        //- Human-readable one-line summary
        string info() const;
};


} // End namespace Foam

#endif
