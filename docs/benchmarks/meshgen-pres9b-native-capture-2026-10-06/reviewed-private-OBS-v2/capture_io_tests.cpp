#include "../literal_split_session_before.hpp"
#ifdef TEST_OLD_HEADER
#include "ProducerStateCapture.before.hpp"
#else
#include "ProducerStateCapture.hpp"
#endif
#include <fstream>
#include <iostream>
#include <sys/stat.h>
#include <sys/resource.h>
#include <csignal>
std::string read(const std::string& p){std::ifstream in(p,std::ios::binary);return {std::istreambuf_iterator<char>(in),{}};}
int main(int argc,char**argv){
 std::string mode=argc>1?argv[1]:"existing";const char*dir=std::getenv("MESHGEN_OBS_PRES9B_DIR");
 if(!dir)return 4;
 std::vector<Vec3> points={{0,0,0},{1,0,0},{1,1,0},{0,1,0},{0,0,1},{1,0,1},{1,1,1}};
 CellRec cell;cell.h=1;cell.faces={{{0,3,2,1},0,-1,{}},{{4,5,6,7},1,-1,{}},
 {{0,1,5,4},2,-1,{}},{{3,7,6,2},3,-1,{}},{{0,4,7,3},4,-1,{}},{{1,2,6,5},5,-1,{}}};
 std::vector<CellRec> cells={cell};CartesianConfig cfg;
 char tail[96];std::snprintf(tail,sizeof(tail),"/producer-stage1-rank0-pid%ld.bin",(long)::getpid());
 const std::string path=std::string(dir)+tail,target=std::string(dir)+"/symlink-target.bin",prior="prior capture bytes: preserve";
 const bool existing=mode=="existing",link=mode=="symlink";
 if(existing){std::ofstream out(path,std::ios::binary);out<<prior;}
 if(link){std::ofstream out(target,std::ios::binary);out<<prior;out.close();if(::symlink(target.c_str(),path.c_str()))return 5;}
 struct stat before{};if(existing||link)if(::lstat(path.c_str(),&before))return 6;
 if(mode=="iofail"){std::signal(SIGXFSZ,SIG_IGN);rlimit r{128,128};if(setrlimit(RLIMIT_FSIZE,&r))return 7;}
 meshgen_producer_capture::Options o;o.stage=1;
 meshgen_producer_capture::capture(cells,points,cfg,3,(1e-12+1e-9)*3,Vec3{},o,
 [](int){return 0;},[](size_t){return 1;},[](long long){return meshgen_producer_capture::LeafBinding{};});
 struct stat after{};const bool present=::lstat(path.c_str(),&after)==0;
 bool pass;
 if(existing||link)pass=present&&before.st_ino==after.st_ino&&read(path)==prior;
 else if(mode=="iofail"||mode=="off")pass=!present;
 else pass=present&&read(path).size()>8&&read(path).substr(read(path).size()-8)=="COMPLETE";
 if(link)pass=pass&&S_ISLNK(after.st_mode)&&read(target)==prior;
 std::cout<<"mode="<<mode<<" pass="<<pass<<" pathPresent="<<present<<" originalTargetPreserved="<<(!link||read(target)==prior)<<"\n";
 return pass?0:1;
}
