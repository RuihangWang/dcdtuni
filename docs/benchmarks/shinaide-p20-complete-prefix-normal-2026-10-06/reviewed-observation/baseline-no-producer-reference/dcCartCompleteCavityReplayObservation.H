#ifndef dcCartCompleteCavityReplayObservation_H
#define dcCartCompleteCavityReplayObservation_H
#include "dcCartCompleteCavityLocalOracle.H"
#include "dcCartCompleteCavityReplaySourceSidecar.H"
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <sstream>
#include <iomanip>
#include <limits>
#include <set>
#include <map>
#include <stdexcept>

// PRIVATE OBSERVATION ONLY. No source/geometry/quality permission is created.
// Called once AFTER current/repair/union decisions; never calls those policies,
// actual geometry calculation, numeric lowering, toPolyMesh, or an authority callback.
namespace Foam { namespace dcCartCompleteCavityReplayObservation
{
namespace L=dcCartCompleteCavityLocalOracle;
namespace O=dcCartLegacyJournalObservation;
struct Limits
{
    static constexpr std::size_t bytes=64u*1024u*1024u;
    static constexpr std::size_t cells=4096,faces=32768,aliases=8192,members=32768;
    static constexpr std::size_t origins=4096,string=4096;
    static constexpr double seconds=10;
};
struct Result
{
    bool enabled=false,complete=false,written=false;
    const char* refusal="disabled";
    std::size_t bytes=0,entries=0,cells=0,faces=0,aliases=0,members=0;
    double seconds=0;
};
inline bool exactFlag(const char* value)noexcept{return value&&value[0]=='1'&&!value[1];}
inline bool enabled()noexcept{return exactFlag(std::getenv("DCCART_COMPLETE_REPLAY_OBSERVATION"));}
struct Writer
{
    using Clock=std::chrono::steady_clock;
    Clock::time_point start=Clock::now();
    std::string text;
    std::size_t cap;
    double seconds;
    explicit Writer(std::size_t c=Limits::bytes,double s=Limits::seconds):cap(c),seconds(s){}
    double elapsed()const{return std::chrono::duration<double>(Clock::now()-start).count();}
    void awake()const{if(!(seconds>0)||elapsed()>=seconds)throw "observation-clock";}
    void put(const std::string& s)
    {
        awake();if(text.size()>cap||s.size()>cap-text.size())throw "observation-byte-cap";
        text+=s;
    }
    void integer(std::int64_t x){put(std::to_string(x));}
    void natural(std::uint64_t x){put(std::to_string(x));}
    void boolean(bool x){put(x?"true":"false");}
    void string(const std::string& s)
    {
        if(s.size()>Limits::string)throw "observation-string-cap";
        put("\"");
        static const char hex[]="0123456789abcdef";
        for(unsigned char c:s)
        {
            if(c=='"'||c=='\\'){put("\\");put(std::string(1,char(c)));}
            else if(c<32){std::string t="\\u00";t+=hex[c>>4];t+=hex[c&15];put(t);}
            else put(std::string(1,char(c)));
        }
        put("\"");
    }
    void scalarValue(scalar x)
    {
        if(!std::isfinite(x))throw "observation-nonfinite";
        static_assert(sizeof(scalar)==8,"binary64 capture required");
        std::uint64_t b=0;std::memcpy(&b,&x,8);
        std::ostringstream os;os<<std::setprecision(17)<<x;
        put("{\"value\":");put(os.str());put(",\"bits\":");
        std::ostringstream hs;hs<<std::hex<<std::setfill('0')<<std::setw(16)<<b;string(hs.str());put("}");
    }
    void pointValue(const point& p){put("[");scalarValue(p.x());put(",");scalarValue(p.y());put(",");scalarValue(p.z());put("]");}
    template<class Seq>void labels(const Seq& xs)
    {put("[");bool first=true;for(auto x:xs){if(!first)put(",");first=false;integer(x);}put("]");}
};
inline void fingerprint(Writer& w,const L::Fingerprint& f)
{
    w.put("{\"raw\":");w.string(f.raw);w.put(",\"roles\":");w.string(f.roles);
    w.put(",\"materialKey\":");w.string(f.materialKey);w.put(",\"material\":");w.string(f.material);
    w.put(",\"producer\":");w.string(f.producer);w.put(",\"boundaryMembership\":");w.string(f.boundaryMembership);w.put("}");
}
inline void origin(Writer& w,const L::Origin& x)
{
    if(x.sourceFacets.size()>L::Caps::faces)throw "observation-origin-facet-cap";
    w.put("{\"nativeParent\":");w.integer(x.nativeParent);w.put(",\"rank\":");w.integer(x.rank);
    w.put(",\"level\":");w.integer(x.level);w.put(",\"leaf\":");w.natural(x.leaf);w.put(",\"piece\":");w.natural(x.piece);
    w.put(",\"region\":");w.string(x.region);w.put(",\"sourceFacets\":[");
    bool first=true;for(const auto& f:x.sourceFacets){if(!first){w.put(",");}first=false;w.labels(f);}w.put("]}");
}
inline void alias(Writer& w,const L::Alias& a)
{
    if(a.members.size()!=a.rawWorld.size()||a.members.size()>Limits::members)throw "observation-alias-shape";
    w.put("{\"representative\":");w.integer(a.representative);w.put(",\"world\":");w.pointValue(a.world);
    w.put(",\"members\":");w.labels(a.members);w.put(",\"rawWorld\":[");
    bool first=true;for(const auto& p:a.rawWorld){if(!first){w.put(",");}first=false;w.pointValue(p);}w.put("]}");
}
inline void snapshot(Writer& w,const L::LegacySnapshot& s)
{
    if(!s.complete||s.cells.size()>Limits::cells||s.faces.size()>Limits::faces
       ||s.aliases.size()>Limits::aliases)throw "observation-snapshot-incomplete-or-cap";
    w.put("{\"complete\":true,\"chargedBytes\":");w.natural(s.bytes);w.put(",\"core\":");w.labels(s.core);
    w.put(",\"outside\":");w.labels(s.outside);w.put(",\"secondHalo\":");w.labels(s.secondHalo);
    w.put(",\"cells\":[");bool first=true;
    for(const auto& c:s.cells)
    {
        if(c.rawFaces.size()>L::Caps::faces||c.origins.size()>8)throw "observation-cell-shape";
        if(!first){w.put(",");}first=false;
        w.put("{\"native\":");w.integer(c.native);w.put(",\"rank\":");w.integer(c.rank);w.put(",\"alive\":");w.boolean(c.alive);
        w.put(",\"rawFaces\":");w.labels(c.rawFaces);w.put(",\"origins\":[");bool f=true;
        for(const auto& o:c.origins){if(!f){w.put(",");}f=false;origin(w,o);}w.put("]}");
    }
    w.put("],\"faces\":[");first=true;
    for(const auto& f:s.faces)
    {
        if(f.ring.size()<3||f.ring.size()>L::Caps::ring)throw "observation-ring-cap";
        if(!first){w.put(",");}first=false;
        w.put("{\"native\":");w.integer(f.native);w.put(",\"owner\":");w.integer(f.owner);w.put(",\"neighbour\":");w.integer(f.neighbour);
        w.put(",\"patch\":");w.integer(f.patch);w.put(",\"zone\":");w.integer(f.zone);w.put(",\"alive\":");w.boolean(f.alive);
        w.put(",\"ring\":");w.labels(f.ring);w.put(",\"role\":");w.string(f.role);w.put("}");
    }
    w.put("],\"aliases\":[");first=true;for(const auto& a:s.aliases){if(!first){w.put(",");}first=false;alias(w,a);}w.put("]}");
}
inline void view(Writer& w,const L::View& v)
{
    if(!v.ready()||v.cells.size()>Limits::cells||v.faces.size()>Limits::faces||v.aliases.size()>Limits::aliases)
        throw "observation-view-incomplete-or-cap";
    w.put("{\"epoch\":");w.natural(v.epoch);w.put(",\"rank\":");w.integer(v.actualRank);w.put(",\"nativeCells\":");w.integer(v.nativeCells);
    w.put(",\"nativePoints\":");w.integer(v.nativePoints);w.put(",\"nGridPoints\":");w.integer(v.nGridPoints);
    w.put(",\"source\":");fingerprint(w,v.source);w.put(",\"core\":");w.labels(v.core);w.put(",\"outside\":");w.labels(v.outside);
    w.put(",\"secondHalo\":");w.labels(v.secondHalo);w.put(",\"origins\":[");bool first=true;
    for(const auto& o:v.origins){if(!first){w.put(",");}first=false;origin(w,o);}w.put("],\"cells\":[");first=true;
    for(const auto& c:v.cells)
    {
        if(c.rawFaces.size()>L::Caps::faces||c.faces.size()>L::Caps::faces)throw "observation-view-cell-cap";
        if(!first){w.put(",");}first=false;
        w.put("{\"native\":");w.integer(c.native);w.put(",\"ordinal\":");w.integer(c.ordinal);w.put(",\"rawFaces\":");w.labels(c.rawFaces);
        w.put(",\"writerFaceAccumulationOrder\":");w.labels(c.faces);w.put(",\"centre\":");w.pointValue(c.centre);
        w.put(",\"volume\":");w.scalarValue(c.volume);w.put(",\"determinant\":");w.scalarValue(c.determinant);w.put("}");
    }
    w.put("],\"faces\":[");first=true;
    for(const auto& f:v.faces)
    {
        if(f.rawRing.size()>L::Caps::ring||f.ring.size()>L::Caps::ring)throw "observation-view-ring-cap";
        if(!first){w.put(",");}first=false;
        w.put("{\"native\":");w.integer(f.native);w.put(",\"ordinal\":");w.integer(f.ordinal);
        w.put(",\"owner\":");w.integer(f.owner);w.put(",\"neighbour\":");w.integer(f.neighbour);
        w.put(",\"rawOwner\":");w.integer(f.rawOwner);w.put(",\"rawNeighbour\":");w.integer(f.rawNeighbour);
        w.put(",\"ownerOrdinal\":");w.integer(f.ownerOrdinal);w.put(",\"neighbourOrdinal\":");w.integer(f.neighbourOrdinal);
        w.put(",\"patch\":");w.integer(f.patch);w.put(",\"zone\":");w.integer(f.zone);
        w.put(",\"internal\":");w.boolean(f.internal);w.put(",\"coupled\":");w.boolean(f.coupled);
        w.put(",\"determinantSelected\":");w.boolean(f.determinantSelected);w.put(",\"metric\":");w.boolean(f.metric);
        w.put(",\"immutablePhysicalSourceRole\":");w.boolean(f.immutablePhysicalSourceRole);
        w.put(",\"rawRing\":");w.labels(f.rawRing);w.put(",\"normalRing\":");w.labels(f.ring);
        w.put(",\"area\":");w.pointValue(f.area);w.put(",\"centre\":");w.pointValue(f.centre);w.put(",\"quality\":[");
        for(unsigned k=0;k<4;++k){if(k)w.put(",");w.scalarValue(f.quality[k]);}w.put("],\"patchRole\":");w.string(f.patchRole);w.put("}");
    }
    w.put("],\"aliases\":[");first=true;for(const auto& a:v.aliases){if(!first){w.put(",");}first=false;alias(w,a);}w.put("]}");
}
struct Selection {const L::View* view=nullptr;const O::Record* event=nullptr;const O::DependencyBinding* binding=nullptr;};
inline Selection select(const L::Catalogue& catalogue,const O::Store& store,const O::DependencyStorage& sidecar)
{
    if(!store.enabled||store.used>23)throw "dependency-observer-disabled-or-invalid";
    Selection found;std::uint64_t best=std::numeric_limits<std::uint64_t>::max();
    for(unsigned i=0;i<store.used;++i)
    {
        const auto& r=store.records[i];const auto& b=sidecar.samples[i];
        if(r.phase!=unsigned(O::Phase::census)||r.reason!=unsigned(O::Reason::dependency)||!b.valid||!b.lookupBounded||b.clockUnknown)continue;
        if(r.ordinal<best){best=r.ordinal;found.event=&r;found.binding=&b;}
        else if(r.ordinal==best)throw "ambiguous-dependency-ordinal";
    }
    if(!found.event)throw "no-observed-complete-dependency";
    for(const auto& v:catalogue.views)
    {
        const auto& b=*found.binding;
        if(v.epoch==found.event->epoch&&v.actualRank==store.rank&&!v.core.empty()&&v.core.front()==b.coreFirst
           &&v.core.size()==b.viewCounts[0]&&v.outside.size()==b.viewCounts[1]&&v.secondHalo.size()==b.viewCounts[2]
           &&v.origins.size()==b.viewCounts[3]&&v.aliases.size()==b.viewCounts[4])
        {if(found.view)throw "ambiguous-view-binding";found.view=&v;}
    }
    if(!found.view)throw "missing-view-binding";
    return found;
}
inline void build(Writer& w,const L::Catalogue& catalogue,const L::Cut& raw,
                  const std::vector<std::vector<label>>& cf,const wordList& patchTypes,
                  const O::Store& store,const O::DependencyStorage& sidecar,Result& result)
{
    const auto chosen=select(catalogue,store,sidecar);const auto& v=*chosen.view;
    if(!v.legacyJournal){throw "missing-legacy-journal";}
    const auto& j=*v.legacyJournal;
    if(!j.complete||j.epoch!=v.epoch||j.rank!=v.actualRank||j.nativeCells!=v.nativeCells||j.nativePoints!=v.nativePoints||j.nGridPoints!=v.nGridPoints||!L::sameFingerprint(j.source,v.source)
       ||j.commits.empty()||j.commits.size()>L::LegacyJournalCaps::entries)throw "incomplete-legacy-journal";
    if(raw.nCells!=v.nativeCells||raw.points.size()!=std::size_t(v.nativePoints)||raw.nGridPoints!=v.nGridPoints
       ||cf.size()!=std::size_t(raw.nCells)||raw.cellAlive.size()!=cf.size()||raw.cellRank.size()!=cf.size()
       ||raw.faces.size()!=raw.owner.size()||raw.faces.size()!=raw.neighbour.size()
       ||raw.faces.size()!=raw.patch.size()||raw.faces.size()!=raw.zone.size()||raw.faces.size()!=raw.faceAlive.size()
       ||j.alias.size()!=raw.points.size()||j.next.size()!=raw.points.size()||j.head.size()!=raw.points.size())
        throw "current-raw-capacity-or-alias-index";
    // Deliberate bounded superset: every committed journal entry is captured.
    // This includes ALL intersections, without re-running the dependency policy.
    const auto& producer=dcCartCompleteCavityReplaySourceSidecar::reference();
    if(!producer.snapshot||producer.rank!=v.actualRank)throw "missing-normal-producer-reference";
    const auto& source=*producer.snapshot;
    if(!source.complete||!source.completedFluidOnlyPath||source.rawSha256!=v.source.raw
       ||source.rolesDigest!=v.source.roles||source.materialKey!=v.source.materialKey
       ||source.materialDigest!=v.source.material||source.producerDigest!=v.source.producer
       ||source.boundaryMembershipDigest!=v.source.boundaryMembership)throw "stale-normal-producer-reference";
    std::set<label> full,metricCells,faces,metricFaces,reps,sourceParents,sourceFaces,sourcePoints;
    std::map<label,std::vector<label>> liveOrigins;
    std::map<label,L::Origin> originCatalog;
    auto remember=[&](const L::Origin& o)
    {
        w.awake();if(o.sourceFacets.size()>512||o.region.size()>256)throw "observation-origin-shape";
        if(o.nativeParent<0||o.nativeParent>=label(source.parents.size())||o.nativeParent>=label(source.valid.size())
           ||!source.valid[o.nativeParent])throw "recorded-origin-source-missing";
        const auto& parent=source.parents[o.nativeParent];
        if(std::get<0>(parent.identity)!=o.rank||std::get<1>(parent.identity)!=o.nativeParent
           ||std::get<2>(parent.identity)!=o.level||std::get<3>(parent.identity)!=o.leaf
           ||std::get<4>(parent.identity)!=o.piece||o.region!=std::string(polyMesh::defaultRegion)
           ||parent.facets.size()!=o.sourceFacets.size())throw "recorded-origin-source-identity";
        for(std::size_t k=0;k<parent.facets.size();++k)
        {
            w.awake();int f=parent.facets[k];
            if(f<0||f>=int(source.faces.size())||source.faces[f].handle!=f
               ||o.sourceFacets[k]!=std::array<label,3>{{f,source.faces[f].patch,source.faces[f].zone}})
               throw "recorded-origin-source-facet";
        }
        auto it=originCatalog.find(o.nativeParent);
        if(it==originCatalog.end()){if(originCatalog.size()>=Limits::origins)throw "origin-catalog-cap";originCatalog.emplace(o.nativeParent,o);}
        else {const auto& x=it->second;if(x.rank!=o.rank||x.level!=o.level||x.leaf!=o.leaf||x.piece!=o.piece
             ||x.region!=o.region||x.sourceFacets!=o.sourceFacets)throw "inconsistent-origin-catalog";}
    };
    auto addCell=[&](label c){w.awake();if(c<0||c>=raw.nCells)throw "raw-cell-range";full.insert(c);if(full.size()>Limits::cells)throw "raw-cell-cap";};
    for(const auto& c:v.cells)addCell(c.native);
    for(const auto& f:v.faces){if(f.native<0||f.native>=label(raw.faces.size()))throw "view-face-range";faces.insert(f.native);if(faces.size()>Limits::faces)throw "raw-face-cap";}
    for(label c:v.core){addCell(c);metricCells.insert(c);}for(label c:v.outside){addCell(c);metricCells.insert(c);}
    for(const auto& o:v.origins)remember(o);
    for(std::size_t i=0;i<j.commits.size();++i)
    {
        w.awake();const auto& e=j.commits[i];
        if(!e.prepared||!e.complete||e.acceptedBefore!=label(i)||e.keep<0||e.drop<0||e.keep==e.drop)
            throw "legacy-sequence-incomplete";
        for(const auto* s:{&e.before,&e.after})
        {
            if(!s->complete)throw "legacy-snapshot-incomplete";
            for(const auto& c:s->cells){addCell(c.native);for(const auto& o:c.origins)remember(o);}
            for(label c:s->core){addCell(c);metricCells.insert(c);}for(label c:s->outside){addCell(c);metricCells.insert(c);}
            for(const auto& f:s->faces){if(f.native<0||f.native>=label(raw.faces.size()))throw "snapshot-face-range";faces.insert(f.native);if(faces.size()>Limits::faces)throw "raw-face-cap";}
        }
    }
    // Current metric cells get full literal faces + complete actual endpoints.
    // Second-halo faces retain shape/Dmask roles but are not invented metrics.
    for(label c:metricCells)if(raw.cellAlive[c])
    {
        w.awake();if(cf[c].size()>L::Caps::faces)throw "raw-cell-face-cap";
        for(label f:cf[c])
        {
            if(f<0||f>=label(raw.faces.size())||!raw.faceAlive[f]||(raw.owner[f]!=c&&raw.neighbour[f]!=c))
                throw "current-cell-incidence";
            metricFaces.insert(f);addCell(raw.owner[f]);if(raw.neighbour[f]>=0)addCell(raw.neighbour[f]);
        }
    }
    for(label c:full)
    {
        w.awake();if(cf[c].size()>L::Caps::faces||(!raw.cellAlive[c]&&!cf[c].empty()))throw "dead-or-oversized-current-cell";
        for(label f:cf[c])
        {
            if(f<0||f>=label(raw.faces.size())||!raw.faceAlive[f]||(raw.owner[f]!=c&&raw.neighbour[f]!=c))throw "current-cell-incidence";
            faces.insert(f);if(faces.size()>Limits::faces)throw "raw-face-cap";
        }
    }
    for(label f:faces)
    {
        w.awake();if(raw.owner[f]<0||raw.owner[f]>=raw.nCells||raw.neighbour[f]<-1||raw.neighbour[f]>=raw.nCells)
            throw "raw-face-endpoint-range";
        if(raw.faces[f].size()<3||raw.faces[f].size()>L::Caps::ring)throw "raw-ring-cap";
        for(label p:raw.faces[f])
        {
            if(p<0||p>=label(raw.points.size())||j.alias[p]<0||j.alias[p]>=label(j.head.size()))throw "raw-alias-range";
            reps.insert(j.alias[p]);if(reps.size()>Limits::aliases)throw "raw-alias-cap";
        }
    }
    for(label c:full)
    {
        w.awake();std::vector<label> parents;bool known=false;
        {O::Scope suppress(false);if(raw.cellAlive[c])known=raw.completeCavityLineage.originsForCells({c},parents);}
        if(raw.cellAlive[c]&&!known)throw "current-origin-key-unknown";
        for(label parent:parents)
        {
            if(parent<0||parent>=label(source.parents.size())||parent>=label(source.valid.size())
               ||!source.valid[parent])throw "current-source-parent-unknown";
            const auto& p=source.parents[parent];
            if(std::get<0>(p.identity)!=v.actualRank||std::get<1>(p.identity)!=parent||p.facets.size()>512
               ||p.facets.size()<4)throw "current-source-parent-identity";
            sourceParents.insert(parent);if(sourceParents.size()>Limits::origins)throw "source-parent-cap";
            L::Origin o;o.nativeParent=parent;o.rank=std::get<0>(p.identity);o.level=std::get<2>(p.identity);
            o.leaf=std::get<3>(p.identity);o.piece=std::get<4>(p.identity);o.region=polyMesh::defaultRegion;
            for(int f:p.facets)
            {
                w.awake();if(f<0||f>=int(source.faces.size())||source.faces[f].handle!=f)throw "current-source-facet-missing";
                const auto& sf=source.faces[f];if(sf.ring.size()<3||sf.ring.size()>512
                    ||(sf.owner!=parent&&sf.neighbour!=parent))throw "current-source-facet-shape";
                o.sourceFacets.push_back({{f,sf.patch,sf.zone}});sourceFaces.insert(f);
                if(sourceFaces.size()>Limits::faces)throw "source-face-cap";
                for(int p:sf.ring){if(p<0||p>=int(source.points.size()))throw "source-point-range";sourcePoints.insert(p);
                    if(sourcePoints.size()>Limits::members)throw "source-point-cap";}
            }
            remember(o);
        }
        liveOrigins.emplace(c,std::move(parents));
    }
    result.entries=j.commits.size();result.cells=full.size();result.faces=faces.size();result.aliases=reps.size();
    w.put("{\"schema\":1,\"observationOnly\":true,\"captureComplete\":true,\"qualityPermission\":false,");
    w.put("\"capturePosition\":\"after-all-current-repair-union-decisions-before-gather-rekey\",\"numericPrefixRecomputed\":false,");
    w.put("\"allJournalEntriesCaptured\":true,\"dependencyOrdinal\":");w.natural(chosen.event->ordinal);
    w.put(",\"dependencySite\":");w.natural(chosen.event->site);w.put(",\"dependencyEntry\":");w.natural(chosen.binding->entry);
    w.put(",\"dependencyKind\":");w.natural(chosen.binding->kind);w.put(",\"dependencySide\":");w.natural(chosen.binding->side);
    w.put(",\"legacyMembership\":");w.natural(chosen.binding->legacyMembership);w.put(",\"viewMembership\":");w.natural(chosen.binding->viewMembership);
    w.put(",\"selectedView\":");view(w,v);
    w.put(",\"journal\":{\"epoch\":");w.natural(j.epoch);w.put(",\"rank\":");w.integer(j.rank);
    w.put(",\"nativeCells\":");w.integer(j.nativeCells);w.put(",\"nativePoints\":");w.integer(j.nativePoints);
    w.put(",\"nGridPoints\":");w.integer(j.nGridPoints);w.put(",\"complete\":");w.boolean(j.complete);
    w.put(",\"workSeconds\":");w.scalarValue(j.workSeconds);w.put(",\"chargedBytes\":");w.natural(j.payloadBytes);
    w.put(",\"source\":");fingerprint(w,j.source);w.put(",\"entries\":[");
    for(std::size_t i=0;i<j.commits.size();++i)
    {
        if(i){w.put(",");}const auto& e=j.commits[i];
        w.put("{\"index\":");w.natural(i);w.put(",\"keep\":");w.integer(e.keep);w.put(",\"drop\":");w.integer(e.drop);
        w.put(",\"acceptedBefore\":");w.integer(e.acceptedBefore);w.put(",\"prepared\":");w.boolean(e.prepared);w.put(",\"complete\":");w.boolean(e.complete);
        w.put(",\"before\":");snapshot(w,e.before);w.put(",\"after\":");snapshot(w,e.after);w.put("}");
    }
    w.put("]},\"currentRaw\":{\"metricCells\":");w.labels(metricCells);w.put(",\"metricFaces\":");w.labels(metricFaces);
    w.put(",\"lineageRevision\":");w.natural(raw.completeCavityLineage.revision());
    w.put(",\"lineageActive\":");w.boolean(raw.completeCavityLineage.active());w.put(",\"cells\":[");bool first=true;
    for(label c:full)
    {
        if(!first){w.put(",");}first=false;const auto& parents=liveOrigins.at(c);
        w.put("{\"native\":");w.integer(c);w.put(",\"alive\":");w.boolean(raw.cellAlive[c]);w.put(",\"rank\":");w.integer(raw.cellRank[c]);
        w.put(",\"metric\":");w.boolean(metricCells.count(c));w.put(",\"rawFaces\":");w.labels(cf[c]);w.put(",\"origins\":[");
        bool f=true;for(label p:parents)
        {
            auto it=originCatalog.find(p);if(it==originCatalog.end())throw "current-origin-record-missing";
            if(!f){w.put(",");}f=false;origin(w,it->second);
        }
        w.put("]}");
    }
    w.put("],\"faces\":[");first=true;
    for(label f:faces)
    {
        if(!first){w.put(",");}first=false;std::string role;
        if(raw.patch[f]>=0)
        {if(raw.patch[f]>=patchTypes.size())throw "raw-patch-role-range";role=patchTypes[raw.patch[f]];}
        else if(raw.patch[f]==-1&&raw.neighbour[f]>=0)role="internal";else throw "raw-face-role-unknown";
        w.put("{\"native\":");w.integer(f);w.put(",\"owner\":");w.integer(raw.owner[f]);w.put(",\"neighbour\":");w.integer(raw.neighbour[f]);
        w.put(",\"patch\":");w.integer(raw.patch[f]);w.put(",\"zone\":");w.integer(raw.zone[f]);w.put(",\"alive\":");w.boolean(raw.faceAlive[f]);w.put(",\"role\":");w.string(role);
        w.put(",\"metric\":");w.boolean(metricFaces.count(f));w.put(",\"rawRing\":");w.labels(raw.faces[f]);w.put(",\"aliasRing\":[");
        for(std::size_t k=0;k<raw.faces[f].size();++k){if(k)w.put(",");w.integer(j.alias[raw.faces[f][k]]);}w.put("]}");
    }
    w.put("],\"aliasClasses\":[");first=true;std::size_t members=0;
    for(label rep:reps)
    {
        if(!first){w.put(",");}first=false;
        w.put("{\"representative\":");w.integer(rep);w.put(",\"members\":[");bool f=true;
        std::set<label> visited;
        for(label p=j.head[rep];p>=0;p=j.next[p])
        {
            w.awake();if(p>=label(j.next.size())||j.next[p]<-1||j.alias[p]!=rep||!visited.insert(p).second)throw "raw-alias-cycle-or-membership";
            if(++members>Limits::members)throw "raw-alias-member-cap";
            if(!f){w.put(",");}f=false;
            w.put("{\"native\":");w.integer(p);w.put(",\"world\":");w.pointValue(raw.points[p]);w.put("}");
        }
        if(visited.empty()||!visited.count(rep)){throw "raw-alias-representative-missing";}
        w.put("]}");
    }
    result.members=members;w.put("]},\"normalOriginCatalog\":[");first=true;
    for(const auto& p:originCatalog){if(!first){w.put(",");}first=false;origin(w,p.second);}w.put("],");
    w.put("\"normalProducer\":{\"complete\":true,\"completedFluidOnlyPath\":true,\"referenceRank\":");w.integer(producer.rank);
    w.put(",\"boundSameFreshSource\":true,\"parents\":[");first=true;
    for(label parent:sourceParents)
    {
        if(!first){w.put(",");}first=false;const auto& p=source.parents[parent];
        w.put("{\"native\":");w.integer(parent);w.put(",\"identity\":[");w.integer(std::get<0>(p.identity));w.put(",");
        w.integer(std::get<1>(p.identity));w.put(",");w.integer(std::get<2>(p.identity));w.put(",");
        w.natural(std::get<3>(p.identity));w.put(",");w.natural(std::get<4>(p.identity));
        w.put("],\"valid\":");w.boolean(source.valid[parent]);w.put(",\"facets\":");w.labels(p.facets);w.put("}");
    }
    w.put("],\"faces\":[");first=true;
    for(label f:sourceFaces)
    {
        if(!first){w.put(",");}first=false;const auto& sf=source.faces[f];
        w.put("{\"handle\":");w.integer(sf.handle);w.put(",\"owner\":");w.integer(sf.owner);w.put(",\"neighbour\":");w.integer(sf.neighbour);
        w.put(",\"patch\":");w.integer(sf.patch);w.put(",\"zone\":");w.integer(sf.zone);w.put(",\"ring\":");w.labels(sf.ring);w.put("}");
    }
    w.put("],\"points\":[");first=true;
    for(label p:sourcePoints){if(!first){w.put(",");}first=false;w.put("{\"native\":");w.integer(p);w.put(",\"world\":");
        const auto& q=source.points[p];w.pointValue(point(q[0],q[1],q[2]));w.put("}");}
    w.put("]},\"currentMetricFieldValues\":\"not-recomputed-observation-only\",\"requiresInstalledReplayGuard\":true}\n");
}
inline bool exclusiveWrite(const char* path,const std::string& bytes)noexcept
{
    FILE* f=std::fopen(path,"wx");if(!f)return false;
    const bool all=std::fwrite(bytes.data(),1,bytes.size(),f)==bytes.size();
    const bool flushed=std::fflush(f)==0;const bool closed=std::fclose(f)==0;
    return all&&flushed&&closed;
}
inline Result capture(bool on,const char* path,const L::Catalogue& catalogue,const L::Cut& raw,
                      const std::vector<std::vector<label>>& cf,const wordList& patchTypes,
                      const O::Store& store,const O::DependencyStorage& sidecar)
{
    Result r;if(!on)return r;r.enabled=true;Writer w;
    try
    {
        if(!path||!*path)throw "observation-path-missing";
        build(w,catalogue,raw,cf,patchTypes,store,sidecar,r);
        w.awake();r.complete=true;r.refusal="none";r.bytes=w.text.size();r.written=exclusiveWrite(path,w.text);
        if(!r.written){r.complete=false;r.refusal="observation-exclusive-io";}
    }
    catch(const char* reason){r.refusal=reason;}
    catch(...){r.refusal="observation-exception";}
    r.seconds=w.elapsed();
    if(!r.complete&&path&&*path&&!r.written)
    {
        // Discard partial payload. Explicit refusal is never labelled complete.
        std::string failure="{\"schema\":1,\"observationOnly\":true,\"captureComplete\":false,\"partialPayloadDiscarded\":true,\"qualityPermission\":false,\"refusal\":\"";
        failure+=r.refusal;failure+="\",\"seconds\":"+std::to_string(r.seconds)+",\"partialBytes\":"+std::to_string(w.text.size())+"}\n";
        r.written=exclusiveWrite(path,failure);r.bytes=failure.size();
    }
    return r;
}
}}
#endif
