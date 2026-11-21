#ifndef dcCartPlannedConeDefectCensus_H
#define dcCartPlannedConeDefectCensus_H
#include "point.H"
#include "symmTensor.H"
#include "dcCartPlannedConeGeometry.H"
#include "dcCartConformingConeSides.H"
#include <map>
#include <set>
#include <vector>
#include <functional>
#include <limits>
#include <cmath>

namespace Foam { namespace dcCartPlannedConeDefectCensus
{
struct Counts
{
    label eligible=0,children=0,badChildren=0,unknownChildren=0;
};
// Geometry query only. No mesh, source permission, point birth or writer mutation.
template<class Mesh>
bool collect
(
    const Mesh& m,const std::vector<std::vector<label>>& bases,
    const std::vector<point>& apex,const std::vector<unsigned char>& eligible,
    const std::vector<bool>& polygonalWarpBase,const std::vector<bool>& polygonalPlanarBase,
    const std::vector<std::vector<label>>& rawCf,
    bool conformingSideUnion,bool planarOutlineEnabled,
    const std::function<bool(label)>& coupledType,
    const std::function<bool()>& available,std::vector<label>& roots,Counts& counts,
    std::map<label,label>* observedParentBadChildren=nullptr
)
{
    namespace S=dcCartSharedAxisSeamVolumePartition;
    roots.clear();counts=Counts();
    if(observedParentBadChildren)observedParentBadChildren->clear();
    if(bases.size()!=apex.size()||bases.size()!=eligible.size()
        ||bases.size()!=rawCf.size()
        ||m.points.size()>=std::size_t(std::numeric_limits<label>::max()))return false;
    const label ap=m.points.size();
    auto clock=[&](){return !available||available();};
    auto finite=[](const vector& p)
    {return std::isfinite(p.x())&&std::isfinite(p.y())&&std::isfinite(p.z());};
    for(label parent=0;parent<label(bases.size());++parent)
    {
        if(!clock())return false;
        if(!eligible[parent]||bases[parent].empty())continue;
        ++counts.eligible;
        dcCartPlannedConeGeometry::Parent plan;
        if(!dcCartPlannedConeGeometry::build(m,parent,bases,apex[parent],
            polygonalWarpBase,polygonalPlanarBase,rawCf,conformingSideUnion,
            planarOutlineEnabled,coupledType,available,plan))return false;
        if(!plan.known){counts.unknownChildren+=plan.children.size();continue;}
        bool badParent=false;label parentBadChildren=0;
        for(const auto& shell:plan.children)
        {
            if(!clock())return false;++counts.children;
            dcCartPlannedConeGeometry::Metrics measured;
            if(!dcCartPlannedConeGeometry::measure(m,plan,shell,measured))
            {++counts.unknownChildren;continue;}
            const auto& sf=measured.sf;const scalar volume=measured.volume;
            const scalar selectedArea=measured.selectedArea;
            const label selectedCount=measured.selectedCount;
            const vector& sum=measured.sum;
            const scalar mean=selectedArea/selectedCount;symmTensor tensor(Zero);
            for(std::size_t j=0;j<shell.size();++j)
                if(shell[j].selected)tensor+=sqr(sf[j]/mean);
            const scalar determinant=mag(det(tensor))/8;
            const scalar aspect=max(cmptMax(sum)/(cmptMin(sum)+ROOTVSMALL),
                cmptSum(sum)/(6*std::pow(max(ROOTVSMALL,volume),scalar(2)/3)));
            if(!std::isfinite(determinant)||!std::isfinite(aspect))
            {++counts.unknownChildren;continue;}
            if(determinant<.001||aspect>1000){++counts.badChildren;++parentBadChildren;badParent=true;}
        }
        // Count only already measured, finite D/AR failures. Unknown children
        // add no invented failures; an unknown plan has no map entry.
        if(observedParentBadChildren)(*observedParentBadChildren)[parent]=parentBadChildren;
        if(badParent)roots.push_back(parent);
    }
    return clock();
}
} }
#endif
