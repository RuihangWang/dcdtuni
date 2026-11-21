/*---------------------------------------------------------------------------*\
  dcCartMeshBuilder — turns a balanced dcCartOctree plus an alive/dead mask
  into an OpenFOAM polyMesh, in one pass.

  This is the step snappyHexMesh spreads over a dozen polyTopoChange rebuilds.
  Here the octree is already final before a single face is created, so the mesh
  is assembled exactly once.

  Face generation rule
  --------------------
  Every face is emitted from ONE side only, so no face is created twice:

    * neighbour one level FINER  -> skip; the four finer cells emit it
    * neighbour one level COARSER-> emit here (the finer side owns it)
    * neighbour at the SAME level-> emit only on the +x/+y/+z side
    * no live neighbour          -> boundary face, emit here

  Every emitted face is therefore a plain rectangle at the emitting cell's own
  level. A coarse cell facing refined neighbours simply ends up with 9, 12 or
  15 faces instead of 6 — exactly the shape hexRef8 produces.

  Hanging points
  --------------
  A rectangle's own four corners are not enough. Where a neighbouring cell is
  refined, its extra corner lands in the middle of this face's edge, and a
  polyMesh face must list every point that lies on its perimeter or checkMesh
  reports a face-face T-junction. After all corner points are registered, each
  face's four edges are walked by recursive midpoint subdivision and any point
  that some other face already registered is spliced in. That is why the face
  vertex counts come out as 4 / 5 / 6 rather than all 4.

  Ordering
  --------
  Internal faces come first in upper-triangular order (by owner, then by
  neighbour), boundary faces follow grouped per patch — the ordering polyMesh
  requires. Face normals point from owner to neighbour; the vertex list is
  reversed when the cell-id comparison puts the owner on the far side.

\*---------------------------------------------------------------------------*/

#ifndef dcCartMeshBuilder_H
#define dcCartMeshBuilder_H

#include "dcCartOctree.H"
#include "faceList.H"
#include "pointField.H"
#include "labelList.H"
#include "boolList.H"
#include "wordList.H"
#include "labelList.H"

#include "autoPtr.H"
#include <functional>
#include <vector>

namespace Foam
{

class polyMesh;
class Time;

class dcCartMeshBuilder
{
public:

    //- Decides which boundary patch an exposed face belongs to.
    //  Called once per boundary face. `cellIdx`/`level` address the LIVE cell,
    //  and (cmpt, positive) the side that is exposed.
    class patchResolver
    {
    public:
        virtual ~patchResolver() = default;

        virtual label patchId
        (
            label level,
            const labelVector& cellIdx,
            direction cmpt,
            bool positive,
            const point& faceCentre,
            bool domainBoundary
        ) const = 0;
    };

    //- Tags INTERNAL faces that lie on a zero-thickness baffle.
    //  createBaffles later splits each zone into a cyclic master/slave pair,
    //  so without these zones the raised-floor and perforated-tile physics is
    //  simply absent from the mesh.
    class faceZoneResolver
    {
    public:
        virtual ~faceZoneResolver() = default;

        //- Zone index for the face of cell (level, idx) on side
        //  (cmpt, positive), or -1 when the face is on no baffle.
        virtual label faceZoneId
        (
            label level,
            const labelVector& cellIdx,
            direction cmpt,
            bool positive
        ) const = 0;
    };

    //- Every exposed face goes to patch 0. Used before carving exists.
    class singlePatchResolver
    :
        public patchResolver
    {
    public:
        virtual label patchId
        (
            label, const labelVector&, direction, bool, const point&, bool
        ) const override
        {
            return 0;
        }
    };


private:

    // Private data

        const dcCartOctree& tree_;

        //- Level of each cell, in final mesh cell order
        labelList cellLevel_;

        //- Octree index of each cell, in final mesh cell order
        List<labelVector> cellIndex_;

        //- Assembled mesh primitives
        pointField points_;
        faceList faces_;
        labelList owner_;
        labelList neighbour_;

        //- Per-patch face counts and start offsets into faces_
        labelList patchSizes_;
        labelList patchStarts_;
        wordList patchNames_;
        wordList patchTypes_;

        label nInternalFaces_;

        //- Final face indices per faceZone, and the zone names
        wordList faceZoneNames_;
        List<labelList> faceZoneFaces_;


    // Private Member Functions

        //- No copy construct/assign
        dcCartMeshBuilder(const dcCartMeshBuilder&) = delete;
        void operator=(const dcCartMeshBuilder&) = delete;


public:

    // Constructors

        //- Build from `tree`, keeping the cells flagged in `alive`.
        //  `alive` is indexed by dcCartOctree::flatten order.
        dcCartMeshBuilder
        (
            const dcCartOctree& tree,
            const boolList& alive,
            const wordList& patchNames,
            const wordList& patchTypes,
            const patchResolver& resolver,
            const wordList& faceZoneNames = wordList(),
            const faceZoneResolver* zoneResolver = nullptr
        );


    // Member Functions

        // Access

            inline const pointField& points() const { return points_; }
            inline const faceList& faces() const { return faces_; }
            inline const labelList& faceOwner() const { return owner_; }
            inline const labelList& faceNeighbour() const { return neighbour_; }
            inline label nInternalFaces() const { return nInternalFaces_; }
            inline label nCells() const { return cellLevel_.size(); }
            inline const labelList& cellLevel() const { return cellLevel_; }
            inline const List<labelVector>& cellIndex() const { return cellIndex_; }
            inline const labelList& patchSizes() const { return patchSizes_; }
            inline const labelList& patchStarts() const { return patchStarts_; }
            inline const wordList& patchNames() const { return patchNames_; }
            inline const wordList& patchTypes() const { return patchTypes_; }
            inline const wordList& faceZoneNames() const { return faceZoneNames_; }
            inline const List<labelList>& faceZoneFaces() const { return faceZoneFaces_; }


        // Output

            //- Construct the polyMesh (caller owns it)
            autoPtr<polyMesh> mesh(const Time& runTime, const word& regionName) const;

            //- The corners of the stair-step mesh and nothing else: every
            //  point the full build registers (same ids and coordinates),
            //  which of them `wanted` picks, and for each picked point that
            //  `collect` accepts, the live cells whose faces list it (in no
            //  particular order). No face is stored.
            static void cornerCells
            (
                const dcCartOctree& tree,
                const boolList& alive,
                const std::function<bool(const point&)>& wanted,
                const std::function<bool(label)>& collect,
                pointField& points,
                labelList& cellLevel,
                List<labelVector>& cellIndex,
                std::vector<bool>& wantedPoint,
                std::vector<std::vector<label>>& pointCells
            );


        //- Only requested finest-grid vertices registered by the stair-step
        //  face topology. Incident leaves are found in the eight integer-grid
        //  octants, including coarse cells at hanging edge/face points.
        //  requestedKeys is sorted and unique; no full face mesh is emitted.
        static void requestedCornerCells
        (
            const dcCartOctree& tree,
            const boolList& alive,
            const std::vector<uint64_t>& requestedKeys,
            const std::function<bool(label)>& collect,
            pointField& points,
            labelList& cellLevel,
            List<labelVector>& cellIndex,
            std::vector<bool>& wantedPoint,
            std::vector<std::vector<label>>& pointCells
        );

        // Info

            string info() const;
};


} // End namespace Foam

#endif
