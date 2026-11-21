#pragma once
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include <fcntl.h>
#include <unistd.h>
namespace meshgen_producer_capture {
constexpr size_t kMaxCells=16384,kMaxPoints=65536,kMaxFaces=262144,kMaxRefs=1048576;
constexpr size_t kMaxBytes=64u*1024u*1024u,kMaxEntries=2;
struct LeafBinding { bool valid=false; uint64_t key=0; int level=-1; std::array<double,6> box{}; };
struct Options { int rank=0,size=1; bool distributed=false; int stage=0; long onlyCi=-1; bool allGreenOnly=false; bool pointGridBuilt=false; size_t pointGridSize=0; };
inline bool enabled() noexcept {
 static const bool value=[](){const char*e=std::getenv("MESHGEN_OBS_PRES9B");return e&&std::strcmp(e,"1")==0;}();
 return value;
}
struct Writer {
 int fd=-1;size_t bytes=0;bool good=true,retain=false,created=false;std::string path;
 ~Writer(){if(fd>=0)::close(fd);if(created&&!retain&&!path.empty())::unlink(path.c_str());}
 void raw(const void*p,size_t n)noexcept {
  if(!good||n>kMaxBytes-bytes){good=false;return;}
  const char*q=(const char*)p;
  while(n){ssize_t w=::write(fd,q,n);if(w<=0){good=false;return;}q+=w;n-=(size_t)w;bytes+=(size_t)w;}
 }
 void u64(uint64_t v)noexcept {unsigned char b[8];for(size_t i=0;i<8;++i)b[i]=(unsigned char)(v>>(i*8));raw(b,8);}
 void i64(int64_t v)noexcept {u64((uint64_t)v);}
 void f64(double d)noexcept {uint64_t v;static_assert(sizeof(v)==sizeof(d),"binary64");std::memcpy(&v,&d,8);u64(v);}
 template<class V>void vec(const V&v)noexcept {f64(v.x);f64(v.y);f64(v.z);}
};
inline void missing(const Options&o,const char*reason)noexcept {
 std::fprintf(stderr,"[PRODUCER-CAPTURE] stage=%d rank=%d complete=0 reason=%s\n",o.stage,o.rank,reason);
}
template<class Cells,class Points,class Config,class Vec,class Owner,class Filled,class Leaf>
void capture(const Cells&cells,const Points&points,const Config&cfg,double H,double clipTol,
 const Vec&origin,const Options&o,Owner owner,Filled filled,Leaf leaf)noexcept {
 if(!enabled())return;
 try {
  static size_t entries=0;
  if(o.onlyCi>=0)return; // Main pre/post entry only; no per-driven-cell capture.
  if(entries>=kMaxEntries){missing(o,"entry_cap");return;}
  ++entries;
  const char*dir=std::getenv("MESHGEN_OBS_PRES9B_DIR");
  if(!dir||dir[0]!='/'){missing(o,"absolute_owned_directory_required");return;}
  if(cells.size()>kMaxCells||points.size()>kMaxPoints){missing(o,"cell_or_point_cap");return;}
  size_t faces=0,refs=0;
  for(const auto&c:cells){
   if(c.faces.size()>kMaxFaces-faces){missing(o,"face_cap");return;}faces+=c.faces.size();
   for(const auto&f:c.faces){if(f.pts.size()>kMaxRefs-refs){missing(o,"reference_cap");return;}refs+=f.pts.size();}
  }
  // Fixed schema worst-case preflight, including all fields, leaf binding and footer.
  const size_t estimated=1024+points.size()*24+cells.size()*192+faces*64+refs*8;
  if(estimated>kMaxBytes){missing(o,"byte_cap");return;}
  char tail[96];std::snprintf(tail,sizeof(tail),"/producer-stage%d-rank%d-pid%ld.bin",o.stage,o.rank,(long)::getpid());
  const std::string path=std::string(dir)+tail;
  Writer w;w.path=path;w.fd=::open(path.c_str(),O_WRONLY|O_CREAT|O_EXCL,0600);
  if(w.fd<0){missing(o,"open_exclusive_failed");return;}
  w.created=true;
  const char magic[8]={'M','G','P','C','A','P','1',0};w.raw(magic,8);
  w.u64(1);w.u64(0x0102030405060708ull);
  const char sourceBase[]="4cd6aab4259fdc15339e35bf27f28de52b394d9949319f0b20cce3a5c74c0eed";
  w.raw(sourceBase,64);
  w.i64(o.rank);w.i64(o.size);w.u64(o.distributed);w.i64(o.stage);w.i64(o.onlyCi);w.u64(o.allGreenOnly);w.u64(o.pointGridBuilt);w.u64(o.pointGridSize);
  w.f64(H);w.f64(clipTol);w.f64(cfg.mergeVolFrac);w.vec(origin);
  w.i64(cfg.baseLevel);w.i64(cfg.maxLevel);w.i64(cfg.mergeMaxPasses);w.i64(cfg.maxGlobalCells);
  w.u64(cfg.qualityGateWarn);w.u64(cfg.dcMode);w.u64(cfg.mode2d);
  w.u64(points.size());w.u64(cells.size());w.u64(faces);w.u64(refs);
  for(const auto&p:points)w.vec(p);
  for(size_t ci=0;ci<cells.size();++ci){
   const auto&c=cells[ci];w.u64(ci);w.i64(c.level);w.i64(c.leaf);w.f64(c.h);w.f64(c.vol);w.vec(c.cen);
   uint64_t flags=(uint64_t)c.cut|((uint64_t)c.merged<<1)|((uint64_t)c.splitPiece<<2)|((uint64_t)c.removed<<3)|((uint64_t)c.mergeBlocked<<4);
   w.u64(flags);w.i64(c.nAbsorb);w.i64(owner((int)ci));w.i64(filled(ci));
   const auto lb=leaf(c.leaf);w.u64(lb.valid);w.u64(lb.key);w.i64(lb.level);for(double d:lb.box)w.f64(d);
   w.u64(c.faces.size());
   for(const auto&f:c.faces){w.i64(f.patch);w.i64(f.nbr);w.vec(f.home);w.u64(f.pts.size());for(int id:f.pts)w.i64(id);}
  }
  const char end[8]={'C','O','M','P','L','E','T','E'};w.raw(end,8);
  bool closed=(::close(w.fd)==0);w.fd=-1;
  if(!w.good||!closed){::unlink(path.c_str());missing(o,"io_incomplete_dropped");return;}
  w.retain=true;
  std::fprintf(stderr,"[PRODUCER-CAPTURE] stage=%d rank=%d complete=1 bytes=%zu path=%s\n",o.stage,o.rank,w.bytes,path.c_str());
 }catch(...){missing(o,"exception_dropped");}
}
} // namespace meshgen_producer_capture
