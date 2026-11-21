// PRIVATE observer: no mesh/side/permission writes and no MPI.
namespace dcCartCapBirthObservation
{
using Mesh=Foam::dcCartCutCells::cutMesh;
using Address=Foam::dcCartSurfaces::preparedFacetAddress;
using Query=Foam::dcCartSurfaces::preparedFacetObservation;
struct State
{
    bool on=false,failed=false,saturated=false;std::size_t rows=0,bytes=0,passes=0,dropped=0,contextsOmitted=0,steps=0,finalRows=0,finalBytes=0;double seconds=0;const char* failure="none";
    std::vector<Foam::boundBox> windows;
    std::vector<std::pair<label,label>> priorityCells;
    State()
    {
        const char* f=getenv("DCCART_CAP_BIRTH_OBSERVE");const char* e=getenv("DCCART_CAP_BIRTH_WINDOWS");
        if(!f||std::strcmp(f,"1")||!e)return;
        try
        {
            const char* p=e;
            while(*p&&windows.size()<16)
            {
                double x[6];for(int d=0;d<6;++d)
                {
                    while(*p==' '||*p==','||*p==';')++p;char* end=nullptr;x[d]=std::strtod(p,&end);
                    if(end==p||!std::isfinite(x[d]))return;p=end;
                }
                for(int d=0;d<3;++d)if(x[d]>x[d+3])return;
                windows.emplace_back(point(x[0],x[1],x[2]),point(x[3],x[4],x[5]));
                while(*p==' '||*p==','||*p==';')++p;
            }
            if(*p||windows.empty())return;
            const char* priority=getenv("DCCART_CAP_BIRTH_PRIORITY_CELLS");
            if(priority){const char* p=priority;while(*p&&priorityCells.size()<16){char* end=nullptr;long r=std::strtol(p,&end,10);if(end==p||*end!=':'||r<0)return;p=end+1;long c=std::strtol(p,&end,10);if(end==p||c<0)return;p=end;priorityCells.emplace_back(label(r),label(c));if(*p==';')++p;else if(*p)return;}if(*p)return;}
            on=true;
        }catch(...){failed=true;failure="configuration-exception";}
    }
};
static State& state(){static State s;return s;}
static bool available()
{auto& s=state();return s.on&&!s.failed&&!s.saturated&&s.rows<192&&s.bytes<16777216&&s.steps<64000000&&s.seconds<4;}
static bool step(std::size_t count=1)
{auto& s=state();s.steps+=count;if(s.steps>=64000000){s.saturated=true;s.failure="step-cap";return false;}return true;}
struct Measure
{std::chrono::steady_clock::time_point start;bool active=false;Measure(bool a):active(a){if(active)start=std::chrono::steady_clock::now();}~Measure(){if(active){auto& s=state();s.seconds+=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();if(s.seconds>=4){s.saturated=true;s.failure="observer-time-cap";}}}};
static void xyz(std::ostream& out,const point& p)
{
    out<<'[';for(int d=0;d<3;++d){if(d)out<<',';uint64_t b=0;std::memcpy(&b,&p[d],8);out<<'"'<<std::hex<<std::setw(16)<<std::setfill('0')<<b<<std::dec<<'"';}out<<']';
}
static void number(std::ostream& out,scalar value){if(std::isfinite(value))out<<value;else out<<"null";}
static void quote(std::ostream& out,const std::string& text)
{out<<'"';for(unsigned char c:text){if(c=='"'||c=='\\')out<<'\\'<<char(c);else if(c<32)out<<'?';else out<<char(c);}out<<'"';}
static bool priorityCell(label rank,label c)
{for(const auto& p:state().priorityCells)if(p.first==rank&&p.second==c)return true;return false;}
static bool selected(const Foam::boundBox& b)
{if(!b.valid())return false;for(const auto& w:state().windows)if(b.overlaps(w))return true;return false;}
static void emit(label rank,const std::string& row)
{
    auto& s=state();if(!available()||row.size()>1048576||s.bytes+row.size()>16777216){if(s.on){++s.dropped;s.saturated=true;s.failure="output-cap";}return;}
    try{std::ofstream out("cap-birth-rank"+std::to_string(rank)+".jsonl",std::ios::app);if(!out){s.failed=true;return;}out<<row<<'\n';out.flush();if(!out){s.failed=true;return;}++s.rows;s.bytes+=row.size()+1;}catch(...){s.failed=true;}
}
struct Face
{label id=-1,o=-1,n=-1,p=-1,z=-1;std::vector<label> ids;std::vector<point> points;std::vector<int> sides;};
struct Cell
{label id=-1,rank=-1,level=-1;uint64_t leaf=0,piece=0;bool alive=false,orig=false,complete=true;scalar volume=0;point centre=point::zero;std::vector<Face> faces;};
static void face(std::ostream& out,const Face& f)
{
    out<<"{\"id\":"<<f.id<<",\"owner\":"<<f.o<<",\"neighbour\":"<<f.n<<",\"patch\":"<<f.p<<",\"zone\":"<<f.z<<",\"ids\":[";
    for(size_t j=0;j<f.ids.size();++j){if(j)out<<',';out<<f.ids[j];}out<<"],\"pointsBits\":[";
    for(size_t j=0;j<f.points.size();++j){if(j)out<<',';xyz(out,f.points[j]);}out<<"],\"sides\":[";
    for(size_t j=0;j<f.sides.size();++j){if(j)out<<',';out<<f.sides[j];}out<<"]}";
}
static void ring(std::ostream& out,const Mesh& m,const std::vector<label>& poly)
{
    out<<"{\"ids\":[";for(size_t j=0;j<poly.size();++j){if(j)out<<',';out<<poly[j];}out<<"],\"pointsBits\":[";
    for(size_t j=0;j<poly.size();++j){if(j)out<<',';if(poly[j]<0||poly[j]>=label(m.points.size()))out<<"null";else xyz(out,m.points[poly[j]]);}out<<"]}";
}
struct Pass
{
    label rank=-1;std::size_t id=0,nextCap=0,activeCap=0;std::map<label,Cell> cells;label active=-1;bool activeSelected=false;const char* phase="before-clip-phase1";
    Pass(const Mesh& m,const std::vector<signed char>& side,const std::vector<bool>* eligible,label r,const char* capturedPhase="before-clip-phase1"):rank(r),phase(capturedPhase)
    {
        if(!available()||r<0)return;Measure measured(true);
        try
        {
            id=++state().passes;std::map<label,Foam::boundBox> boxes;
            auto candidate=[&](label c){return c>=0&&c<m.nCells&&m.cellAlive[c]&&m.cellRank[c]==r&&(!eligible||(*eligible)[c]);};
            for(label f=0;f<label(m.faces.size());++f)if(m.faceAlive[f])for(label c:{m.owner[f],m.neighbour[f]})if(candidate(c))
            {
                if(!step()){cells.clear();return;}auto i=boxes.emplace(c,Foam::boundBox(Foam::boundBox::invertedBox)).first;
                for(label p:m.faces[f]){if(!step()){cells.clear();return;}if(p>=0&&p<label(m.points.size()))i->second.add(m.points[p]);}
            }
            for(int priority=1;priority>=0;--priority)for(const auto& item:boxes)if(priorityCell(r,item.first)==bool(priority)&&selected(item.second))
            {
                if(cells.size()>=48){++state().contextsOmitted;continue;}
                label c=item.first;Cell v;v.id=c;v.rank=m.cellRank[c];v.level=m.cellLevel[c];v.leaf=m.cellLeaf[c];v.piece=m.cellPiece[c];v.alive=m.cellAlive[c];v.orig=m.cellOrigAlive[c];v.volume=m.cellRefVol[c];v.centre=m.cellRefCentre[c];if(!std::isfinite(v.volume))v.complete=false;for(int d=0;d<3;++d)if(!std::isfinite(v.centre[d]))v.complete=false;cells.emplace(c,std::move(v));
            }
            for(label f=0;f<label(m.faces.size());++f)if(m.faceAlive[f])for(label c:{m.owner[f],m.neighbour[f]})
            {
                if(!step()){cells.clear();return;}auto i=cells.find(c);if(i==cells.end())continue;auto& cell=i->second;
                if(cell.faces.size()>=128||m.faces[f].size()>256){cell.complete=false;continue;}
                Face v;v.id=f;v.o=m.owner[f];v.n=m.neighbour[f];v.p=m.patch[f];v.z=m.zone[f];v.ids=m.faces[f];
                for(label p:v.ids){if(p<0||p>=label(m.points.size())){cell.complete=false;v.points.push_back(point::zero);v.sides.push_back(-999);}else{v.points.push_back(m.points[p]);if(p>=label(side.size()))cell.complete=false;for(int d=0;d<3;++d)if(!std::isfinite(m.points[p][d]))cell.complete=false;v.sides.push_back(p<label(side.size())?int(side[p]):-999);}}
                cell.faces.push_back(std::move(v));
            }
        }catch(...){cells.clear();state().failed=true;}
    }
};
static thread_local Pass* currentPass=nullptr;
struct PassScope{Pass* old;PassScope(Pass* p):old(currentPass){currentPass=p;}~PassScope(){currentPass=old;}};
struct CellScope{Pass* pass;label old;std::size_t oldCap;bool oldSelected;CellScope(Pass* p,label c):pass(p),old(p?p->active:-1),oldCap(p?p->activeCap:0),oldSelected(p?p->activeSelected:false){if(p){p->active=c;p->activeCap=++p->nextCap;p->activeSelected=false;}}~CellScope(){if(pass){pass->active=old;pass->activeCap=oldCap;pass->activeSelected=oldSelected;}}};
struct Source;
static thread_local Source* currentSource=nullptr;
struct Source
{
    Mesh& m;const std::vector<label>& loop;const std::vector<std::pair<std::vector<label>,label>>& pieces;
    const std::vector<chordInsert>& inserts;Pass* pass=nullptr;Cell* cell=nullptr;Source* old=nullptr;
    Query query;std::string facetPayload;const char* branch="entered";
    bool geometryKnown=false,planar=false;scalar off=0,onTol=0;std::size_t rawFacets=0,mineFacets=0;std::vector<std::size_t> mineIndices;
    Source(Mesh& mesh,const std::vector<label>& l,const std::vector<std::pair<std::vector<label>,label>>& p,const std::vector<chordInsert>& i):m(mesh),loop(l),pieces(p),inserts(i)
    {
        pass=currentPass;old=currentSource;if(pass&&available()){auto v=pass->cells.find(pass->active);if(v!=pass->cells.end())cell=&v->second;}if(cell){Foam::boundBox b(Foam::boundBox::invertedBox);for(label p:loop)if(p>=0&&p<label(m.points.size()))b.add(m.points[p]);if(!selected(b))cell=nullptr;}if(cell){currentSource=this;pass->activeSelected=true;}
    }
    void facets(const std::vector<Foam::dcCartSurfaces::triInfo>& tt)
    {
        if(!cell||!available())return;Measure measured(true);try
        {
            rawFacets=tt.size();std::ostringstream out;out.exceptions(std::ios::badbit|std::ios::failbit);out<<'[';
            for(size_t j=0;j<tt.size()&&j<2048;++j)
            {
                if(j)out<<',';const auto& t=tt[j];out<<"{\"region\":"<<t.region<<",\"p\":[";xyz(out,t.p0);out<<',';xyz(out,t.p1);out<<',';xyz(out,t.p2);out<<"],\"normal\":";xyz(out,t.normal);
                if(j<query.rows.size()){const auto& a=query.rows[j];out<<",\"surface\":"<<a.surface<<",\"triangle\":"<<a.triangle<<",\"pointIds\":["<<a.pointIds[0]<<','<<a.pointIds[1]<<','<<a.pointIds[2]<<"],\"geometryEpoch\":"<<a.geometryEpoch<<",\"membershipEpoch\":"<<a.membershipEpoch<<",\"addressValid\":"<<(a.valid?"true":"false")<<",\"identityBound\":"<<(a.identityBound?"true":"false")<<",\"sourceObjectUid\":";quote(out,a.sourceObjectUid);out<<",\"sourceFaceUid\":";quote(out,a.sourceFaceUid);out<<",\"representation\":";quote(out,a.representation);}
                out<<'}';
            }out<<']';facetPayload=out.str();
        }catch(...){cell=nullptr;state().failed=true;}
    }
    void filtered(const std::vector<const Foam::dcCartSurfaces::triInfo*>& mine,const std::vector<Foam::dcCartSurfaces::triInfo>& all)
    {if(!cell||!available())return;try{mineFacets=mine.size();for(const auto* p:mine){if(mineIndices.size()>=2048){query.truncated=true;query.complete=false;break;}mineIndices.push_back(std::size_t(p-all.data()));}}catch(...){state().failed=true;state().failure="filtered-facet-exception";}}
    ~Source()
    {
        if(currentSource==this)currentSource=old;if(!cell||!available())return;Measure measured(true);try
        {
            std::ostringstream out;out.exceptions(std::ios::badbit|std::ios::failbit);out<<std::setprecision(17)<<"{\"schema\":\"cap-birth/v1\",\"stage\":\"source-plan\",\"rank\":"<<pass->rank<<",\"pass\":"<<pass->id<<",\"cap\":"<<pass->activeCap<<",\"cell\":"<<cell->id<<",\"contextPhase\":";quote(out,pass->phase);out<<",\"completeCapturedFootprint\":"<<(cell->complete?"true":"false")<<",\"leaf\":"<<cell->leaf<<",\"piece\":"<<cell->piece<<",\"level\":"<<cell->level<<",\"alive\":"<<(cell->alive?"true":"false")<<",\"origAlive\":"<<(cell->orig?"true":"false")<<",\"refVolumeBits\":";{uint64_t bits=0;std::memcpy(&bits,&cell->volume,8);out<<'"'<<std::hex<<std::setw(16)<<std::setfill('0')<<bits<<std::dec<<'"';}out<<",\"refCentre\":";xyz(out,cell->centre);
            out<<",\"preclipFaces\":[";for(size_t j=0;j<cell->faces.size();++j){if(j)out<<',';face(out,cell->faces[j]);}out<<"],\"loop\":";ring(out,m,loop);out<<",\"branch\":";quote(out,branch);
            out<<",\"geometryKnown\":"<<(geometryKnown?"true":"false")<<",\"planar\":"<<(planar?"true":"false")<<",\"offPlane\":";number(out,off);out<<",\"onTol\":";number(out,onTol);out<<",\"sourceCandidateCount\":"<<rawFacets<<",\"filteredFacetCount\":"<<mineFacets<<",\"facetsComplete\":"<<(query.complete?"true":"false")<<",\"indexCandidates\":"<<query.indexCandidates<<",\"sourceQuerySeconds\":"<<query.querySeconds<<",\"sourceFacets\":"<<(facetPayload.empty()?"[]":facetPayload)<<",\"filteredFacetIndices\":[";for(size_t j=0;j<mineIndices.size();++j){if(j)out<<',';out<<mineIndices[j];}out<<"],\"pieces\":[";
            for(size_t j=0;j<pieces.size();++j){if(j)out<<',';out<<"{\"region\":"<<pieces[j].second<<",\"ring\":";ring(out,m,pieces[j].first);out<<'}';}out<<"],\"inserts\":[";
            for(size_t j=0;j<inserts.size();++j){if(j)out<<',';out<<'['<<inserts[j].a<<','<<inserts[j].b<<','<<inserts[j].x<<']';}out<<"]}";emit(pass->rank,out.str());
        }catch(...){state().failed=true;}
    }
};
static Query* addressSidecar(){return available()&&currentSource&&currentSource->cell?&currentSource->query:nullptr;}
static void append(const Mesh& m,label f,const char* stage="append")
{
    if(!available()||!currentPass||!currentPass->activeSelected||!currentPass->cells.count(currentPass->active))return;Measure measured(true);
    try{std::ostringstream out;out.exceptions(std::ios::badbit|std::ios::failbit);out<<"{\"schema\":\"cap-birth/v1\",\"stage\":";quote(out,stage);out<<",\"rank\":"<<currentPass->rank<<",\"pass\":"<<currentPass->id<<",\"cap\":"<<currentPass->activeCap<<",\"cell\":"<<currentPass->active<<",\"face\":"<<f<<",\"owner\":"<<m.owner[f]<<",\"neighbour\":"<<m.neighbour[f]<<",\"patch\":"<<m.patch[f]<<",\"zone\":"<<m.zone[f]<<",\"ring\":";ring(out,m,m.faces[f]);out<<'}';emit(currentPass->rank,out.str());}catch(...){state().failed=true;}
}
static void coalesce(const Mesh& m,label f1,label f2,const std::vector<label>& proposed,label rank,const char* stage="coalesce-accepted")
{
    if(!available())return;Measure measured(true);Foam::boundBox b(Foam::boundBox::invertedBox);for(label p:proposed)if(p>=0&&p<label(m.points.size()))b.add(m.points[p]);if(!selected(b))return;
    try{std::ostringstream out;out.exceptions(std::ios::badbit|std::ios::failbit);out<<"{\"schema\":\"cap-birth/v1\",\"stage\":";quote(out,stage);out<<",\"rank\":"<<rank<<",\"f1\":"<<f1<<",\"f2\":"<<f2<<",\"owner1\":"<<m.owner[f1]<<",\"neighbour1\":"<<m.neighbour[f1]<<",\"patch1\":"<<m.patch[f1]<<",\"zone1\":"<<m.zone[f1]<<",\"owner2\":"<<m.owner[f2]<<",\"neighbour2\":"<<m.neighbour[f2]<<",\"patch2\":"<<m.patch[f2]<<",\"zone2\":"<<m.zone[f2]<<",\"before1\":";ring(out,m,m.faces[f1]);out<<",\"before2\":";ring(out,m,m.faces[f2]);out<<",\"proposed\":";ring(out,m,proposed);out<<'}';emit(rank,out.str());}catch(...){state().failed=true;}
}
static void finalBase(const Mesh& m,label f,const std::vector<label>& cf,label rank)
{
    if(!state().on)return;
    if(state().finalRows>=16){++state().dropped;state().saturated=true;return;}
    try
    {
        const label c=m.owner[f];std::ostringstream out;out.exceptions(std::ios::badbit|std::ios::failbit);
        out<<"{\"stage\":\"preflight-bad-base\",\"face\":"<<f<<",\"cell\":"<<c<<",\"rank\":"<<rank<<",\"leaf\":"<<m.cellLeaf[c]<<",\"piece\":"<<m.cellPiece[c]<<",\"level\":"<<m.cellLevel[c]<<",\"ring\":";ring(out,m,m.faces[f]);out<<",\"ownerStar\":[";
        const std::size_t count=std::min(cf.size(),std::size_t(128));
        for(size_t j=0;j<count;++j){if(j)out<<',';label g=cf[j];out<<"{\"face\":"<<g<<",\"owner\":"<<m.owner[g]<<",\"neighbour\":"<<m.neighbour[g]<<",\"patch\":"<<m.patch[g]<<",\"zone\":"<<m.zone[g]<<",\"ring\":";ring(out,m,m.faces[g]);out<<'}';}
        out<<"],\"ownerStarComplete\":"<<(count==cf.size()?"true":"false")<<"}\n";
        const std::string row=out.str();if(row.size()>1048576||state().finalBytes+row.size()>4194304){++state().dropped;state().saturated=true;return;}std::ofstream file("cap-birth-final-rank"+std::to_string(rank)+".jsonl",std::ios::app);file<<row;if(!file){state().failed=true;return;}++state().finalRows;state().finalBytes+=row.size();
    }catch(...){state().failed=true;state().failure="final-witness-exception";}
}
static void finish(label rank)
{
    auto& s=state();if(!s.on)return;
    try{std::ofstream out("cap-birth-status-rank"+std::to_string(rank)+".json");out<<std::setprecision(17)<<"{\"enabled\":true,\"failed\":"<<(s.failed?"true":"false")<<",\"saturated\":"<<(s.saturated?"true":"false")<<",\"rows\":"<<s.rows<<",\"bytes\":"<<s.bytes<<",\"dropped\":"<<s.dropped<<",\"contextsOmitted\":"<<s.contextsOmitted<<",\"steps\":"<<s.steps<<",\"finalRows\":"<<s.finalRows<<",\"finalBytes\":"<<s.finalBytes<<",\"observerSeconds\":"<<s.seconds<<",\"completeCollection\":"<<((!s.failed&&!s.saturated&&!s.dropped&&!s.contextsOmitted)?"true":"false")<<",\"reason\":";quote(out,s.failure);out<<"}\n";}catch(...){s.failed=true;}
}

}
