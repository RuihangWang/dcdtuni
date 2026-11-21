#ifndef dcCartWriterNumericContext_H
#define dcCartWriterNumericContext_H
#include "dcCartCutCells.H"
#include "faceList.H"
#include "pointField.H"
#include "DynamicList.H"
namespace Foam
{
// Owned results of the same lowering used by the real writer. Native slots
// and global compact ordinals remain distinct. No authority is implied.
struct dcCartCutCells::writerNumericData
{
    std::vector<label> newCell,newPoint,internalIds,boundaryIds,finalPatch;
    pointField points;
    faceList faces;
    labelList owner,neighbour;
    List<DynamicList<label>> zoneFaces;
    label nC=0,nP=0,nInt=0,nF=0;
};
}
#endif
