#pragma once
// Read-only .cfdrun layout selection shared by every ingest consumer.
// This view never rewrites schemaVersion, geometry, BC or porous metadata.
#include <nlohmann/json.hpp>
#include <algorithm>
#include <cmath>
#include <map>
#include <string>
#include <vector>
#include "meshgen/Vec.hpp"

namespace meshgen {
struct CfdrunGeometryView {
    using Json = nlohmann::json;
    const Json* root = nullptr;
    const Json* geometry = nullptr; // null only for the historical V2 missing-blob fallback
    const Json* config = nullptr;
    int schema = 1;
    bool missingV2Blob = false;
    const Json& geometryOrRoot() const { return geometry ? *geometry : *root; }
    const Json& meshConfig() const {
        static const Json empty = Json::object();
        return config ? *config : empty;
    }
    bool modern() const { return schema >= 3; }

    static bool finiteNumber(const Json& v) {
        return v.is_number() && std::isfinite(v.get<double>());
    }
    static bool vector(const Json& v, Vec3& out) {
        if (v.is_array() && v.size()==3 && finiteNumber(v[0]) &&
            finiteNumber(v[1]) && finiteNumber(v[2])) {
            out={v[0].get<double>(),v[1].get<double>(),v[2].get<double>()}; return true;
        }
        if (v.is_object() && v.contains("x") && v.contains("y") && v.contains("z") &&
            finiteNumber(v["x"]) && finiteNumber(v["y"]) && finiteNumber(v["z"])) {
            out={v["x"].get<double>(),v["y"].get<double>(),v["z"].get<double>()}; return true;
        }
        return false;
    }
    // Schema3/4 frontend room plane is (x,z) encoded as (x,y,0), with Y-up height.
    // Legacy roomBBox arithmetic is retained in CartesianCfdrun.cpp for schema1/2.
    bool roomBounds(Vec3& lo, Vec3& hi) const {
        if (!geometry || !geometry->contains("room")) return false;
        const auto& r=(*geometry)["room"];
        if (!r.is_object() || !r.contains("plane") || !r["plane"].is_array() ||
            r["plane"].size()<3 || !r.contains("height") || !finiteNumber(r["height"]) ||
            r["height"].get<double>()<=0) return false;
        bool first=true;
        for(const auto& p:r["plane"]) {
            if (!p.is_object() || !p.contains("x") || !p.contains("y") ||
                !finiteNumber(p["x"]) || !finiteNumber(p["y"])) return false;
            if(p.contains("z") && (!finiteNumber(p["z"]) || p["z"].get<double>()!=0))return false;
            double x=p["x"].get<double>(), z=p["y"].get<double>();
            if(first){lo={x,0,z};hi={x,r["height"].get<double>(),z};first=false;}
            else {lo.x=std::min(lo.x,x);lo.z=std::min(lo.z,z);
                  hi.x=std::max(hi.x,x);hi.z=std::max(hi.z,z);}
        }
        return lo.x<hi.x && lo.z<hi.z;
    }
    double baseSize(double fallback) const {
        const auto& c=meshConfig();
        for(const char* k:{"baseSize","meshBaseSize"})
            if(c.contains(k) && finiteNumber(c[k]))return c[k].get<double>();
        return fallback;
    }
    const Json* location() const {
        if(geometry && geometry->contains("room") && (*geometry)["room"].is_object() &&
           (*geometry)["room"].contains("locationInMesh"))return &(*geometry)["room"]["locationInMesh"];
        if(config && config->contains("locationInMesh"))return &(*config)["locationInMesh"];
        if(root && root->contains("meshConfig") && (*root)["meshConfig"].is_object() &&
           (*root)["meshConfig"].contains("locationInMesh"))return &(*root)["meshConfig"]["locationInMesh"];
        return nullptr;
    }

    static bool select(const Json& raw, CfdrunGeometryView& out, std::string& error) {
        out={}; error.clear();
        CfdrunGeometryView v;v.root=&raw;
        auto fail=[&](const std::string& msg){error="[cfdrun-view] "+msg;return false;};
        if(!raw.is_object())return fail("root must be an object");
        if(raw.contains("schemaVersion")) {
            const auto& s=raw["schemaVersion"];
            if(!s.is_number_integer())return fail("schemaVersion must be an integer");
            if(s<1 || s>4)return fail("unsupported schemaVersion");
            v.schema=s.get<int>();
        }
        if(v.schema==1)v.geometry=&raw;
        else if(raw.contains("meshGeometry") && raw["meshGeometry"].is_object())v.geometry=&raw["meshGeometry"];
        else if(v.schema==2)v.missingV2Blob=true;
        else return fail("schema3/4 requires object meshGeometry");
        if(v.geometry && v.geometry->contains("meshConfig") && (*v.geometry)["meshConfig"].is_object())
            v.config=&(*v.geometry)["meshConfig"];
        else if(raw.contains("meshConfig") && raw["meshConfig"].is_object())v.config=&raw["meshConfig"];

        // Preserve historical schema1/2 permissive defaults. Strict current formats
        // refuse malformed active data instead of silently switching to legacy.
        if(!v.modern()){out=v;return true;}
        std::vector<const Json*> stack{&raw};
        while(!stack.empty()) {
            const Json* j=stack.back();stack.pop_back();
            if(j->is_number() && !finiteNumber(*j))return fail("nonfinite numeric value");
            if(j->is_structured())for(const auto& e:*j)stack.push_back(&e);
        }
        const auto& g=*v.geometry;
        if(g.contains("meshConfig") && !g["meshConfig"].is_object())return fail("meshGeometry.meshConfig must be an object");
        if(raw.contains("meshConfig") && !raw["meshConfig"].is_object())return fail("root meshConfig must be an object");
        for(const char* cat:{"racks","acus","walls","floors","baffles","partitionWalls","heatSources","wallObjects"}) {
            if(!g.contains(cat))continue;
            if(!g[cat].is_object())return fail(std::string(cat)+" must be a dictionary");
            for(const auto& e:g[cat])if(!e.is_object())return fail(std::string(cat)+" entry must be an object");
        }
        for(const char* key:{"room","racks","acus","walls","floors","baffles","partitionWalls","heatSources","wallObjects"})
            if(raw.contains(key) && g.contains(key) && !raw[key].is_null() && raw[key]!=g[key])
                return fail(std::string("contradictory duplicate geometry.")+key);
        const Json* top=raw.contains("meshConfig")?&raw["meshConfig"]:nullptr;
        const Json* inner=g.contains("meshConfig")?&g["meshConfig"]:nullptr;
        auto validConfig=[&](const Json* c)->bool {
            if(!c)return true;
            for(const char* k:{"baseSize","meshBaseSize"})if(c->contains(k) &&
                (!finiteNumber((*c)[k]) || (*c)[k].get<double>()<=0))return false;
            if(c->contains("baseSize") && c->contains("meshBaseSize") && (*c)["baseSize"]!=(*c)["meshBaseSize"])return false;
            if(c->contains("locationInMesh")){Vec3 x;if(!vector((*c)["locationInMesh"],x))return false;}
            if(c->contains("blockMeshBounds") && !(*c)["blockMeshBounds"].is_null()) {
                const auto& b=(*c)["blockMeshBounds"];Vec3 lo,hi;
                if(!b.is_object() || !b.contains("min") || !b.contains("max") ||
                   !vector(b["min"],lo) || !vector(b["max"],hi) ||
                   !(lo.x<hi.x && lo.y<hi.y && lo.z<hi.z))return false;
            }
            for(const char* k:{"refinementLevel","rackRegionLevel","floorRefinementLevel",
                    "acuRefinementLevel","boxRefinementLevel","partitionWallRefinementLevel","baffleRefinementLevel"}) {
                if(!c->contains(k))continue;
                const auto& a=(*c)[k];
                auto level=[](const Json& x){return x.is_number_integer() && x>=0 && x<=20;};
                if(a.is_array()) {
                    if(a.empty() || a.size()>2)return false;
                    for(const auto& x:a)if(!level(x))return false;
                    if(a.size()==2 && a[0]>a[1])return false;
                } else if(!level(a))return false;
            }
            for(const char* k:{"maxGlobalCells","meshProcess","meshTimeLimitSeconds"})
                if(c->contains(k) && (!finiteNumber((*c)[k]) || (*c)[k].get<double>()<=0))return false;
            return true;
        };
        if(!validConfig(top) || !validConfig(inner))return fail("invalid numeric/vector/bounds/level meshConfig");
        if(top && inner) {
            double base=0;bool got=false;
            for(const Json* c:{top,inner})for(const char* k:{"baseSize","meshBaseSize"})if(c->contains(k)){
                double now=(*c)[k].get<double>();if(got && now!=base)return fail("contradictory duplicate base size");base=now;got=true;
            }
            for(auto it=top->begin();it!=top->end();++it) {
                const auto& key=it.key();
                // UI null bounds may be replaced by derived bounds. Non-null
                // duplicate settings must agree, including actual explicit bounds.
                if(key=="baseSize" || key=="meshBaseSize" || key=="locInMeshVisible" ||
                   key=="blockMeshEditActive" || key=="blockMeshMoveMode")continue;
                if(inner->contains(key) && !it.value().is_null() && !(*inner)[key].is_null() &&
                   it.value()!=(*inner)[key])return fail("contradictory duplicate meshConfig."+key);
            }
        }
        if(g.contains("room")) {
            const auto& r=g["room"];Vec3 lo,hi;
            if(!v.roomBounds(lo,hi))return fail("invalid Y-up room plane/height");
            if(r.contains("upAxis") && r["upAxis"]!="y")return fail("unsupported room upAxis");
            for(const char* k:{"locationInMesh"})if(r.contains(k)){Vec3 x;if(!vector(r[k],x))return fail("invalid room seed");}
            if(r.contains("extraLocationsInMesh")) {
                if(!r["extraLocationsInMesh"].is_array())return fail("extraLocationsInMesh must be an array");
                for(const auto& x:r["extraLocationsInMesh"]){Vec3 p;if(!vector(x,p))return fail("invalid extra seed");}
            }
            if(r.contains("locationInMesh"))for(const Json* c:{top,inner})if(c && c->contains("locationInMesh")){
                Vec3 a,b;vector(r["locationInMesh"],a);vector((*c)["locationInMesh"],b);
                if(a.x!=b.x || a.y!=b.y || a.z!=b.z)return fail("contradictory duplicate locationInMesh");
            }
        }
        // Metadata stays in raw. Conflicting repeated bindings cannot silently
        // become whichever record happens to be visited last by a consumer.
        for(const char* dict:{"boundaryBindings","porousBindings"}) {
            if(!raw.contains(dict))continue;
            if(!raw[dict].is_object())return fail(std::string(dict)+" must be a dictionary");
            std::map<std::string,Json> seen;
            for(const auto& b:raw[dict]) {
                if(!b.is_object())return fail(std::string(dict)+" entry must be an object");
                Json sig=Json::object();
                for(const char* key:{"bcType","type","role","parameters"})if(b.contains(key))sig[key]=b[key];
                for(const char* key:{"solidName","patchName","surfaceUid"})if(b.contains(key)) {
                    if(!b[key].is_string())return fail(std::string(dict)+" identity must be string");
                    const std::string id=std::string(key)+":"+b[key].get<std::string>();
                    if(id==std::string(key)+":")continue;
                    auto q=seen.emplace(id,sig);
                    if(!q.second && q.first->second!=sig)return fail(std::string(dict)+" conflicting duplicate "+id);
                }
            }
        }
        out=v;return true;
    }
};
} // namespace meshgen
