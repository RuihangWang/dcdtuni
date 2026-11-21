#ifndef dcCartAsciiSTLPrecision_H
#define dcCartAsciiSTLPrecision_H
#include <array>
#include <cerrno>
#include <cctype>
#include <cmath>
#include <cstdlib>
#include <istream>
#include <iterator>
#include <string>
#include <vector>

namespace dcCartAsciiSTLPrecision
{
// Coordinate-only replay. Official STLReader owns format/regions/facet order.
inline bool readPoints(std::istream& input,
    std::vector<std::array<double,3>>& out, std::string* why=nullptr)
{
    auto reject=[&](const char* reason)->bool
    { if(why){try{*why=reason;}catch(...){}} return false; };
    try
    {
        const std::string text((std::istreambuf_iterator<char>(input)),
                               std::istreambuf_iterator<char>());
        if(input.bad())return reject("ascii-stream");
        std::size_t pos=0; std::vector<std::array<double,3>> points;
        auto token=[&](std::string& value)->bool
        {
            while(pos<text.size() && std::isspace(static_cast<unsigned char>(text[pos])))++pos;
            if(pos==text.size()){value.clear();return false;}
            const std::size_t begin=pos;
            while(pos<text.size() && !std::isspace(static_cast<unsigned char>(text[pos])))++pos;
            value.assign(text,begin,pos-begin);return true;
        };
        auto lower=[](std::string value)->std::string
        {for(char& c:value)c=static_cast<char>(std::tolower(static_cast<unsigned char>(c)));return value;};
        auto expect=[&](const char* value)->bool
        {std::string word;return token(word)&&lower(word)==value;};
        auto number=[&](double& value)->bool
        {
            std::string word;if(!token(word))return false;
            errno=0;char* end=nullptr;value=std::strtod(word.c_str(),&end);
            return end!=word.c_str() && end && *end=='\0' && errno!=ERANGE && std::isfinite(value);
        };
        auto nameLine=[&]()
        {while(pos<text.size() && text[pos]!='\n')++pos;if(pos<text.size())++pos;};
        bool inSolid=false,sawSolid=false;std::string word;
        while(token(word))
        {
            word=lower(word);
            if(!inSolid)
            {
                if(word!="solid")return reject("ascii-solid");
                nameLine();inSolid=true;sawSolid=true;continue;
            }
            if(word=="endsolid"){nameLine();inSolid=false;continue;}
            if(word!="facet" || !expect("normal"))return reject("ascii-facet");
            double normal=0;
            for(int d=0;d<3;++d)if(!number(normal))return reject("ascii-normal");
            if(!expect("outer") || !expect("loop"))return reject("ascii-outer-loop");
            for(int p=0;p<3;++p)
            {
                if(!expect("vertex"))return reject("ascii-three-vertices");
                std::array<double,3> point;
                for(int d=0;d<3;++d)if(!number(point[d]))return reject("ascii-coordinate");
                points.push_back(point);
            }
            if(!expect("endloop") || !expect("endfacet"))return reject("ascii-end-facet");
        }
        if(!sawSolid || inSolid)return reject("ascii-incomplete-solid");
        out.swap(points);if(why)why->clear();return true;
    }
    catch(...){return reject("ascii-replay-exception");}
}
}
#endif
