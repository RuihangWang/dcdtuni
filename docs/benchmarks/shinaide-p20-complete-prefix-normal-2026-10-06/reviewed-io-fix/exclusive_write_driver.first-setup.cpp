#include "dcCartCompleteCavityReplayObservation.H"
#include <sys/resource.h>
#include <signal.h>
#include <fstream>
#include <iostream>
#include <sstream>
#include <cstring>
#include <cerrno>
namespace R=Foam::dcCartCompleteCavityReplayObservation;
static int replacementMode=0;static const char* replacementPath=nullptr;
static const char* replacementTarget=nullptr;static const char* replacementReady=nullptr;
extern "C" size_t __real_fwrite(const void*,size_t,size_t,FILE*);
extern "C" size_t __wrap_fwrite(const void* ptr,size_t size,size_t count,FILE* f)
{
 const size_t wrote=__real_fwrite(ptr,size,count,f);
 const int mode=replacementMode;replacementMode=0;
 if(mode&&replacementPath)
 {
  ::unlink(replacementPath);
  if(mode==1)::symlink(replacementTarget,replacementPath);
  else if(mode==2)::rename(replacementReady,replacementPath);
 }
 return wrote;
}
static bool exists(const std::string& p){struct stat s{};return ::lstat(p.c_str(),&s)==0;}
static bool linkIs(const std::string& p,const std::string& target)
{char b[4096]{};const ssize_t n=::readlink(p.c_str(),b,sizeof(b)-1);return n>=0&&std::string(b,n)==target;}
static std::string read(const std::string& p){std::ifstream f(p);std::ostringstream s;s<<f.rdbuf();return s.str();}
static bool seed(const std::string& p,const std::string& b)
{FILE* f=std::fopen(p.c_str(),"wb");if(!f)return false;const bool a=std::fwrite(b.data(),1,b.size(),f)==b.size();return std::fclose(f)==0&&a;}
static int passed=0,failed=0;
static void check(const char* name,bool ok)
{std::cout<<"{\"case\":\""<<name<<"\",\"pass\":"<<(ok?"true":"false")<<"}\n";ok?++passed:++failed;}
int main(int argc,char** argv)
{
 if(argc!=2)return 2;const std::string d=argv[1];
 const std::string target=d+"/existing-target",existing=d+"/existing-file",link=d+"/existing-link",dangling=d+"/existing-dangling";
 if(!seed(target,"target-original")||!seed(existing,"existing-original")||::symlink(target.c_str(),link.c_str())||::symlink((d+"/absent-target").c_str(),dangling.c_str()))return 3;
 const std::string replacementReadyFile=d+"/replacement-ready";if(!seed(replacementReadyFile,"replacement-original"))return 4;
 struct rlimit saved{},next{},actual{};if(::getrlimit(RLIMIT_FSIZE,&saved))return 5;next=saved;next.rlim_cur=1024;
 if(::signal(SIGXFSZ,SIG_IGN)==SIG_ERR||::setrlimit(RLIMIT_FSIZE,&next)||::getrlimit(RLIMIT_FSIZE,&actual))return 6;
 check("actualRLIMIT_FSIZE1024",actual.rlim_cur==1024);
 const std::string partial=d+"/partial";errno=0;const bool first=R::exclusiveWrite(partial.c_str(),std::string(8192,'X'));const int ioErrno=errno;struct stat partialStat{};const bool remains=::lstat(partial.c_str(),&partialStat)==0;
 std::cout<<"{\"rlimitFailureReturn\":"<<(first?"true":"false")<<",\"errno\":"<<ioErrno<<",\"pathExistsAfterFailure\":"<<(remains?"true":"false")<<",\"partialSize\":"<<(remains?partialStat.st_size:-1)<<"}\n";
 check("ownedPartialPathRemoved",!first&&!remains);
 const bool refusal=R::exclusiveWrite(partial.c_str(),"{\"captureComplete\":false}\n");
 check("samePathExplicitRefusalCanBeWritten",refusal&&read(partial)=="{\"captureComplete\":false}\n");
 check("preExistingFileNotDeletedOrChanged",!R::exclusiveWrite(existing.c_str(),std::string(8192,'Y'))&&exists(existing)&&read(existing)=="existing-original");
 check("preExistingSymlinkAndTargetHeld",!R::exclusiveWrite(link.c_str(),std::string(8192,'Y'))&&linkIs(link,target)&&read(target)=="target-original");
 check("preExistingDanglingSymlinkHeld",!R::exclusiveWrite(dangling.c_str(),std::string(8192,'Y'))&&linkIs(dangling,d+"/absent-target"));
 const std::string normal=d+"/complete";
 check("successfulCreatedFileHeld",R::exclusiveWrite(normal.c_str(),"complete")&&read(normal)=="complete");
 const std::string replaced=d+"/owned-replaced-symlink";replacementPath=replaced.c_str();replacementTarget=target.c_str();replacementMode=1;
 const bool symlinkWrite=R::exclusiveWrite(replaced.c_str(),std::string(8192,'Z'));
 check("failedCreatedPathReplacedBySymlinkNotDeleted",!symlinkWrite&&linkIs(replaced,target)&&read(target)=="target-original");
 const std::string replacedRegular=d+"/owned-replaced-regular";replacementPath=replacedRegular.c_str();replacementReady=replacementReadyFile.c_str();replacementMode=2;
 const bool regularWrite=R::exclusiveWrite(replacedRegular.c_str(),std::string(8192,'Z'));
 check("failedCreatedPathReplacedByDifferentRegularNotDeleted",!regularWrite&&exists(replacedRegular)&&read(replacedRegular)=="replacement-original");
 ::setrlimit(RLIMIT_FSIZE,&saved);
 std::cout<<"{\"summary\":true,\"passed\":"<<passed<<",\"failed\":"<<failed<<"}\n";return failed?1:0;
}
