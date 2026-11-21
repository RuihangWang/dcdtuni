"""Fail-closed per-run source-role authority for experimental point repair."""
from __future__ import annotations
import hashlib,json,re
from pathlib import Path
SCOPE="globallyNoBulkSourcesAndNoMaterialDomains"
HEX=re.compile(r"[0-9a-f]{64}\Z")
def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
def sanitize(value):return re.sub(r"[^A-Za-z0-9_]","_",value)
def clean(text):
    return re.sub(r"/\*.*?\*/|//[^\n]*","",text,flags=re.S)
def entries(text):
    """Parse direct entries of the runner-generated, no-directive dictionary subset."""
    tokens=re.findall(r'"[^"\n]*"|[{}();]|[^\s{}();]+',clean(text));blocks_out={};values={};seen=set();i=0
    while i<len(tokens):
        key=tokens[i].strip('"');i+=1
        if key.startswith("#") or key in seen or key in "{}();":raise ValueError("unsupported/duplicate dictionary entry")
        seen.add(key)
        if i<len(tokens) and tokens[i]=="{":
            start=i+1;depth=1;i+=1
            while i<len(tokens) and depth:
                depth+=(tokens[i]=="{")-(tokens[i]=="}");i+=1
            if depth:raise ValueError("unclosed dictionary block")
            blocks_out[key]=" ".join(tokens[start:i-1])
        else:
            start=i;stack=[]
            while i<len(tokens):
                t=tokens[i]
                if t=="(" or t=="{":
                    if t=="{" and not stack:raise ValueError("unsupported dictionary scalar")
                    stack.append(t)
                elif t in [")","}"]:
                    if not stack or stack.pop()!=("(" if t==")" else "{"):raise ValueError("unbalanced dictionary value")
                if t==";" and not stack:break
                i+=1
            if i==len(tokens) or stack:raise ValueError("unterminated dictionary scalar")
            values[key]=" ".join(tokens[start:i]);i+=1
    return blocks_out,values
def blocks(text):return entries(text)[0]
def scalar(body,key,default=None):
    value=entries(body)[1].get(key)
    if value is None:return default
    if not re.fullmatch(r'[^\s{}();]+',value):raise ValueError("non-scalar "+key)
    return value.strip('"')
def _forbidden_material(value,path=""):
    bad=[]
    if isinstance(value,dict):
        for k,v in value.items():
            name=re.sub(r"[_-]","",k).lower()
            if name in {"material","materials","materialdomains","cellzone","cellzones","pointzone","pointzones","facezones","fvoptions","sourcevolumes","porouszones","multiregion","zones"}:
                if v not in (None,{},[],False,""):bad.append(path+"/"+k)
            bad.extend(_forbidden_material(v,path+"/"+k))
    elif isinstance(value,list):
        for i,v in enumerate(value):bad.extend(_forbidden_material(v,path+"/"+str(i)))
    return bad
# Compatibility extras used by the normal exporter/runner are explicit, not a
# permissive recursive allow-list. Unknown extras disable this experiment.
EXTRA_FIELDS={
    "CFDExport":{"inheritLiquidResults","liquidCooling"},
    "CFDExportRack":{"frameSolidNames","safeMarginTop","safeMarginBottom","partitions","wallBaffleSolids","blankingPanelType"},
    "RackGeometry":{"quaternion","scale","inletOffset","outletOffset"},
    "SimpleGeometry":{"quaternion","scale"},
    "RackServer":{"faces","interfaceSolidNames"},
    "ServerPhysics":{"inlet","outlet"},
    "CFDExportWall":{"isRoomBoundary"},
    "SensorPoint":{"name","enabled"},
    "CFDExportMeshConfig":{"acuRefinementLevel","boxRefinementLevel","baffleRefinementLevel","defaultFeatureLevel","acuFeatureLevel","boxFeatureLevel","baffleFeatureLevel","blockMeshBounds","refinementZones"},
    "CFDExportSimulationConfig":{"startFrom","transientResidualControl"},
}
def known_extra(typ,key,value):
    def subset(obj,keys):
        return isinstance(obj,dict) and set(obj)<=set(keys)
    def vec(obj,keys):
        return subset(obj,keys) and set(obj)==set(keys) and all(isinstance(v,(float,int)) and not isinstance(v,bool) for v in obj.values())
    if key=="quaternion":return vec(value,"xyzw")
    if key=="scale":return vec(value,"xyz")
    if key in {"frameSolidNames","wallBaffleSolids","interfaceSolidNames"}:return isinstance(value,list)and all(isinstance(v,str)for v in value)
    if key=="partitions":return value==[]
    if key in {"inletOffset","outletOffset","safeMarginTop","safeMarginBottom"}:return isinstance(value,(float,int))and not isinstance(value,bool)
    if key in {"inheritLiquidResults","isRoomBoundary","enabled"}:return isinstance(value,bool)
    if key in {"blankingPanelType","name","startFrom"}:return isinstance(value,str)
    if key=="faces":
        return subset(value,{"inlet","outlet","top","bottom","left","right"})and all(subset(face,{"solid","wallSolid","patches"})and isinstance(face.get("patches",[]),list)and all(subset(patch,{"solid","patchId"})for patch in face.get("patches",[]))for face in value.values())
    if typ=="ServerPhysics":return subset(value,{"bcType","temperature","power","flow_rate","flowRate","sigma","timeVarying"})and all(not isinstance(v,(dict,list))for v in value.values())
    if key=="liquidCooling":
        if not subset(value,{"enabled","fluid","cdus","piping"})or not isinstance(value.get("cdus",{}),dict):return False
        if not subset(value.get("fluid",{}),{"type","rho","mu","cp","k"}):return False
        for cdu in value.get("cdus",{}).values():
            if not subset(cdu,{"geometry","supplyTemperature","totalFlowRate","servedRacks"}):return False
            g=cdu.get("geometry",{})
            if not subset(g,{"anchor","size"})or not vec(g.get("anchor",{}),"xyz")or not vec(g.get("size",{}),"WDH"):return False
        pipe=value.get("piping",{})
        return subset(pipe,{"autoGenerated","mainPipeSpec","branchPipeSpec","chains"})and pipe.get("chains",[])==[]
    if key=="blockMeshBounds":return value is None or(subset(value,{"min","max"})and all(vec(v,"xyz")for v in value.values()))
    if key=="refinementZones":
        return isinstance(value,list)and all(subset(row,{"name","min","max","level"})and vec(row.get("min",{}),"xyz")and vec(row.get("max",{}),"xyz")for row in value)
    if key=="transientResidualControl":return subset(value,{"p","p_rgh","U","T","k","epsilon","omega","k_epsilon"})and all(isinstance(v,(float,int))for v in value.values())
    if key.endswith("RefinementLevel"):return isinstance(value,list)and all(isinstance(v,int)for v in value)
    if key.endswith("FeatureLevel"):return isinstance(value,int)and not isinstance(value,bool)
    return False
def unknown_model_extras(model,path=""):
    bad=[]
    if hasattr(type(model),"model_fields"):
        typ=type(model).__name__
        for key,value in(getattr(model,"__pydantic_extra__",{})or{}).items():
            if key not in EXTRA_FIELDS.get(typ,set())or not known_extra(typ,key,value):bad.append(path+"/"+key)
        for key in type(model).model_fields:
            bad.extend(unknown_model_extras(getattr(model,key,None),path+"/"+key))
    elif isinstance(model,dict):
        for key,value in model.items():bad.extend(unknown_model_extras(value,path+"/"+str(key)))
    elif isinstance(model,list):
        for i,value in enumerate(model):bad.extend(unknown_model_extras(value,path+"/"+str(i)))
    return bad
def classified_any_fields(model):
    """Reject unclassified Any payloads; source geometry/configs use typed adapters."""
    import typing
    def has_any(annotation):
        return annotation is typing.Any or any(has_any(a)for a in typing.get_args(annotation))
    def harmless(typ,key,value):
        if value is None or value=={} or value==[]:return True
        if typ=="CfdrunV2" and key=="meshGeometry":return True  # CFDExport recursively checked.
        if typ in {"SimulationConfig","CFDExportSimulationConfig"}:
            if key=="monitoring":
                return isinstance(value,dict)and set(value)<={"residuals","serverTemperature","acuReturnTemperature"}and isinstance(value.get("residuals",False),bool)and value.get("serverTemperature",[])==[]and value.get("acuReturnTemperature",[])==[]
            if "Per" in key and key.endswith("Level"):
                return isinstance(value,dict)and all(isinstance(k,str)and k.isdigit()and(isinstance(v,int)and not isinstance(v,bool)or isinstance(v,list)and len(v)==2 and all(isinstance(x,int)and not isinstance(x,bool)for x in v))for k,v in value.items())
        if typ=="PostprocessConfig":
            if key=="probes":
                return isinstance(value,dict)and all(isinstance(row,dict)and set(row)<={"name","location","enabled"}and isinstance(row.get("location"),dict)and set(row["location"])==set("xyz")and all(isinstance(v,(int,float))and not isinstance(v,bool)for v in row["location"].values())for row in value.values())
            if key=="labels":return isinstance(value,dict)and set(value)<={"sensorMeasurements"}and value.get("sensorMeasurements",{})=={}
        return False
    bad=[]
    if hasattr(type(model),"model_fields"):
        for key,field in type(model).model_fields.items():
            value=getattr(model,key,None)
            if has_any(field.annotation)and not harmless(type(model).__name__,key,value):bad.append(type(model).__name__+"."+key)
            bad.extend(classified_any_fields(value))
    elif isinstance(model,dict):
        for value in model.values():bad.extend(classified_any_fields(value))
    elif isinstance(model,list):
        for value in model:bad.extend(classified_any_fields(value))
    return bad
def allowed_entries(body,keys):
    actual_blocks,actual_values=entries(body)
    if (set(actual_blocks)|set(actual_values))-set(keys):raise ValueError("unknown generated dictionary semantics")
def source_lineage(raw,file_id,solid_name,operation):
    """Bind a named region to the verified original file/name or a known transform."""
    solids=raw["geometryExport"].get("solids",{})
    surfaces=raw["geometryExport"].get("sourceSurfaces",{})
    descriptor_keys=sorted(k for k,v in solids.items()if v.get("solidName")==solid_name)
    source_keys=sorted(k for k,v in surfaces.items()if v.get("stlFileId")==file_id and v.get("solidName")==solid_name)
    if operation=="roomEnclosure":
        matched=[k for k,v in(raw.get("meshGeometry")or{}).get("floors",{}).items()if k==solid_name and v.get("stlFile")=="merged_floor_ceiling.stl"and v.get("defaultBC")=="wall"]
        if not matched:raise ValueError("room enclosure lacks declared wall floor source")
        kind="normalRoomBoundaryFromDeclaredFloor"
    elif operation in {"baffleSplit","baffleRegroup"}:
        if not source_keys:raise ValueError("transformed sheet has no original named source")
        kind="originalSheetSource"
    else:
        # Hash equality proves this exact named solid is in the original file;
        # normal runner assigns unlisted solid regions explicit wall patchInfo.
        kind="verifiedOriginalNamedSolid"
    return dict(stlFileId=file_id,solidName=solid_name,kind=kind,descriptorKeys=descriptor_keys,sourceSurfaceKeys=source_keys)
def derive_singleton_authority(raw_bytes,*,locked_raw_sha256,derived_data,resolved_run,actual_region_inventory):
    rawsha=hashlib.sha256(raw_bytes).hexdigest()
    out=dict(version=1,eligible=False,rawSha256=rawsha,sourceSHA256=rawsha,rolesDigest=digest({}),scope=SCOPE,reasons=[])
    def reject(reason):out["reasons"].append(reason)
    try:raw=json.loads(raw_bytes)
    except (ValueError,TypeError):reject("invalidRawJSON");return out
    if not isinstance(raw,dict):reject("invalidRawShape");return out
    if rawsha!=locked_raw_sha256 or not HEX.fullmatch(str(locked_raw_sha256)):reject("lockedRawIdentityMismatch")
    if raw.get("schemaVersion")not in (2,3,4):reject("unsupportedSchema")
    if not isinstance(raw.get("meshGeometry"),dict)or not isinstance(derived_data,dict):reject("missingDerivedGeometry");return out
    try:
        from backend.aircooling.runner.cfdrun_schema import CfdrunV2
        model=CfdrunV2.model_validate(raw)
    except (ValueError,TypeError) as e:
        reject("rawSchemaInvalid:"+type(e).__name__);return out
    from backend.aircooling.runner.cfdrun_schema import CFDExport
    from backend.aircooling.runner.derived_config import execution_blob
    try:
        raw_geometry_model=CFDExport.model_validate(execution_blob(raw))
        derived_geometry_model=CFDExport.model_validate(derived_data)
    except (ValueError,TypeError):
        reject("invalidGeometryProducerStructure");return out
    if unknown_model_extras(raw_geometry_model)or unknown_model_extras(derived_geometry_model):reject("unknownNestedGeometryProducer")
    if classified_any_fields(model)or classified_any_fields(raw_geometry_model)or classified_any_fields(derived_geometry_model):reject("unclassifiedAnyPayload")
    ge=raw.get("geometryExport")or{}
    if any(row.get("kind")!="grouped_ascii"for row in ge.get("stlFiles",{}).values()):reject("unknownOriginalSTLProducerKind")
    if not ge.get("solids")or any(row.get("role")!="wall"for row in ge["solids"].values()):reject("unknownOriginalSolidRole")
    if any(row.get("representation")not in {"zeroThicknessSheet","surfaceFace"}for row in ge.get("sourceSurfaces",{}).values()):reject("unknownOriginalSurfaceRepresentation")
    mg=raw["meshGeometry"]
    known_geometry={"version","source","projectName","room","racks","acus","wallObjects","heatSources","baffles","floors","walls","partitionWalls","meshConfig","inheritLiquidResults","sensorPoints","liquidCooling"}
    if set(mg)-known_geometry:reject("unknownGeometryProducer")
    if _forbidden_material(raw.get("inputs")or{}):reject("unknownInputMaterialOrZoneDeclaration")
    for name,obj in [("raw",mg),("derived",derived_data)]:
        for k in ["partitionWalls","heatSources"]:
            if not isinstance(obj.get(k,{}),dict)or obj.get(k):reject(name+"UnsupportedVolumeProducer:"+k)
        for rack in obj.get("racks",{}).values():
            leak=rack.get("leakage")or{}
            if not isinstance(leak,dict)or leak.get("enabled",False)is not False or leak.get("throughSlots")or leak.get("groups"):reject(name+"RackLeakageProducer")
        if _forbidden_material(obj):reject(name+"UnknownMaterialOrZoneDeclaration")
    if raw.get("sourceTerms")not in ({},None):reject("rawBulkOrOtherSourceTerms")
    if(raw.get("inputs")or{}).get("partitionWalls"):reject("partitionInputOverridesUnsupported")
    if resolved_run is None:reject("missingResolvedRun");return out
    if getattr(resolved_run,"fv_options_inputs",None)or getattr(resolved_run,"source_terms",None):reject("resolvedBulkOrOtherSourceTerms")
    try:registry={name:spec.model_dump(mode="json")for name,spec in resolved_run.bridge_registry.items()}
    except (AttributeError,TypeError):reject("missingResolvedRegistry");return out
    # Independent parameter materialisation from the exact pinned raw model.
    # Full BCSpec equality rejects stale values with otherwise matching role names.
    from backend.aircooling.runner.cfdrun_reader import _materialise_boundary_bindings,_materialise_porous_bindings
    from backend.aircooling.runner.bridge_bc import BridgeBCRegistry
    expected_registry=BridgeBCRegistry()
    _materialise_boundary_bindings(model,expected_registry)
    _materialise_porous_bindings(model,expected_registry)
    exact_registry={name:spec.model_dump(mode="json")for name,spec in expected_registry.items()}
    if registry!=exact_registry:reject("resolvedRegistryNotFromPinnedRaw")
    expected={}
    for row in(raw.get("boundaryBindings")or{}).values():
        name=row.get("patchName")
        if not name:reject("missingBoundaryPatchName");continue
        key=sanitize(name);bc=row.get("bcType")
        if key in expected and expected[key]!=bc:reject("contradictoryBoundaryBinding")
        expected[key]=bc
    pairs={}
    for row in(raw.get("porousBindings")or{}).values():
        kind=row.get("type");name=row.get("patchName")
        if kind not in {"wallBaffle","perforatedBaffle"}or not name:reject("unknownPorousRole");continue
        m=re.fullmatch(r"(.+)_(master|slave)",name)
        if not m:reject("unpairedSheetRole");continue
        stem,side=m.groups();pairs.setdefault(stem,{})[side]=row
        key=sanitize(name);expected[key]="wall_baffle"if kind=="wallBaffle"else"perforated_baffle"
    for pair in pairs.values():
        if set(pair)!={"master","slave"}:reject("incompleteSheetPair");continue
        if any(pair["master"].get(k)!=pair["slave"].get(k)for k in["type","surfaceUid","objectPath","parameters"]):reject("contradictorySheetPair")
    for name,bc in expected.items():
        if registry.get(name,{}).get("bc_type")!=bc:reject("resolvedRoleMismatch:"+name)
    inv=actual_region_inventory
    if not isinstance(inv,dict):reject("missingActualInventory");return out
    if not isinstance(inv.get("files"),dict) or not all(isinstance(row,dict)for row in inv["files"].values()) or not isinstance(inv.get("regions"),list)or not all(isinstance(row,dict)for row in inv["regions"]):reject("invalidActualInventoryShape");return out
    proof=inv.get("verifiedOriginal")
    original_files=(raw.get("geometryExport")or{}).get("stlFiles")or{}
    expected_orig={key:{"path":v["path"],"contentHash":v.get("contentHash")}for key,v in original_files.items()}
    if not expected_orig or any(not HEX.fullmatch(str(v["contentHash"]))for v in expected_orig.values()):reject("missingOriginalSTLHashes")
    if not isinstance(proof,dict)or proof.get("version")!=1 or proof.get("rawSha256")!=rawsha or proof.get("verifiedFiles")!=expected_orig:reject("missingOrStaleOriginalPreflightReceipt")
    files=inv.get("files")or{};regions=inv.get("regions")or[]
    if not files or not regions:reject("emptyActualInventory")
    for name,row in files.items():
        if Path(name).name!=name or not name.endswith(".stl")or not HEX.fullmatch(str(row.get("sha256"))):reject("unsafeOrUnboundActualSTL")
        refs=row.get("sourceFileIds")
        if not refs or any(x not in original_files for x in refs):reject("missingOriginalSTLLineage")
        if row.get("operation")not in {"literal","roomEnclosure","baffleRegroup","baffleSplit"}:reject("unknownSTLTransformation")
        if row.get("operation")=="literal":
            if not any(original_files[k]["path"]==name and original_files[k].get("contentHash")==row["sha256"]for k in refs):reject("literalSTLHashMismatch")
        else:
            source_names={v["solidName"]for v in(raw.get("geometryExport")or{}).get("sourceSurfaces",{}).values()if v.get("stlFileId")in(refs or[])}
            if row.get("operation")=="roomEnclosure":
                if name!="merged_floor_ceiling.stl" or set(row["solidNames"])!={"Room_Floor","Room_Ceiling"} or not mg.get("room"):reject("unknownRoomEnclosureTransform")
            elif row.get("operation")=="baffleRegroup":
                if name!="merged_wall_baffle.stl" or not set(row["solidNames"])<=source_names:reject("unknownBaffleRegroupTransform")
            elif row.get("operation")=="baffleSplit":
                if not name.startswith("wallBaffle_floor_") or not set(row["solidNames"])<=source_names:reject("unknownBaffleSplitTransform")
    seen={};covered={k:set()for k in files}
    for row in regions:
        filename=row.get("file");patch=row.get("patchName");names=row.get("solidNames")or[]
        if filename not in files or not patch or not names:reject("unboundActualRegion");continue
        if any(x not in files[filename]["solidNames"]for x in names):reject("actualRegionNotInSTL")
        covered[filename].update(names)
        if patch in seen:reject("duplicateOrContradictoryActualRegion")
        seen[patch]=row
        expected_lineage=[source_lineage(raw,k,n,files[filename]["operation"])for k in files[filename]["sourceFileIds"]for n in names]
        if row.get("sourceLineage")!=expected_lineage:reject("actualSourceLineageMismatch")
        bc=registry.get(patch,{}).get("bc_type")
        if row.get("origin")=="resolved":
            if bc is None or row.get("bcType")!=bc or patch not in expected:reject("actualResolvedRoleMismatch:"+patch)
        elif row.get("origin")=="generatedWall":
            if row.get("patchType")!="wall"or row.get("bcType")!="wall"or bc not in(None,"wall"):reject("actualGeneratedWallConflict:"+patch)
        else:reject("unknownActualRoleOrigin")
        if row.get("patchType")not in {"wall","patch","cyclic"}:reject("unknownActualPatchType")
    for name,file in files.items():
        if file.get("usage")=="editorOnly":
            from backend.aircooling.runner.mesh_acceptance import is_editor_only_stl
            if not is_editor_only_stl(name) or covered[name]:reject("invalidEditorOnlySTL")
        elif covered[name]!=set(file["solidNames"]):reject("actualRegionCoverageMissing:"+name)
    for name in expected:
        if name not in seen:reject("resolvedPatchAbsentFromActualRegions:"+name)
    if not HEX.fullmatch(str(inv.get("dictionarySHA256"))):reject("missingDictionaryBinding")
    out["rolesDigest"]=digest(dict(files=files,regions=sorted(regions,key=lambda x:(x.get("patchName",""),x.get("file",""))),registry=registry,refinementOnly=inv.get("refinementOnly",{}),sourceSurfaceMetadata=inv.get("sourceSurfaceMetadata",{}),original=proof,dictionarySHA256=inv.get("dictionarySHA256")))
    out["reasons"]=sorted(set(out["reasons"]));out["eligible"]=not out["reasons"]
    return out
def collect_actual_inventory(raw,*,tri_surface_dir,snappy_bytes,wall_pair_zones,verified_original_receipt):
    """Read each actual generated STL once; parse the exact already-generated role tables."""
    directory=Path(tri_surface_dir);text=snappy_bytes.decode();top=blocks(text)
    allowed_entries(text,{"FoamFile","castellatedMesh","snap","addLayers","keepPatches","geometry","castellatedMeshControls","snapControls","addLayersControls","meshQualityControls","mergeTolerance","writeFlags","debug","dcdtuniSourceSurfaces","dcdtuniSourceRegions"})
    allowed_entries(top["castellatedMeshControls"],{"maxLocalCells","maxGlobalCells","minRefinementCells","maxLoadUnbalance","nCellsBetweenLevels","features","refinementSurfaces","resolveFeatureAngle","refinementRegions","locationInMesh","locationsInMesh","allowFreeStandingZoneFaces","handleSnapProblems"})
    geom=blocks(top["geometry"]);ctl=blocks(top["castellatedMeshControls"]);surfs=blocks(ctl["refinementSurfaces"]);refinement=blocks(ctl.get("refinementRegions",""))
    for body in refinement.values():allowed_entries(body,{"mode","levels"})
    for body in surfs.values():
        allowed_entries(body,{"level","regions","patchInfo","faceType","faceZone"})
        if "patchInfo"in blocks(body):allowed_entries(blocks(body)["patchInfo"],{"type","inGroups"})
        for nested in blocks(blocks(body).get("regions","")).values():
            allowed_entries(nested,{"level","patchInfo","faceType","faceZone"})
            for pb in blocks(nested).values():
                if "type" in entries(pb)[1]:allowed_entries(pb,{"type","inGroups"})
    if re.search(r"\bcellZones?\b",clean(text)):raise ValueError("generated bulk cellZone declaration")
    orig=(raw.get("geometryExport")or{}).get("stlFiles")or{}
    bypath={r["path"]:key for key,r in orig.items()};registry_raw={}
    for row in raw.get("boundaryBindings",{}).values():registry_raw[sanitize(row["patchName"])]=row["bcType"]
    for row in raw.get("porousBindings",{}).values():
        registry_raw[sanitize(row["patchName"])]={"wallBaffle":"wall_baffle","perforatedBaffle":"perforated_baffle"}.get(row["type"],"unknown")
    files={};regions=[];refinement_only={}
    known_refinement={"rackRegion_"+sanitize(k)for k in (raw.get("meshGeometry")or{}).get("racks",{})}
    known_refinement.update(sanitize(row["name"])for row in (raw.get("meshConfig")or{}).get("refinementZones",[]))
    for filename,body in geom.items():
        if scalar(body,"type") in {"searchableBox","searchableRotatedBox"}:
            allowed_entries(body,{"type","min","max","origin","e1","e2","e3","span"})
            if filename not in known_refinement or filename not in refinement or scalar(refinement[filename],"mode")!="inside" or filename in surfs:raise ValueError("unknown non-STL geometry semantics")
            refinement_only[filename]={"geometry":body,"refinement":refinement[filename]};continue
        if Path(filename).name!=filename or not filename.endswith(".stl")or scalar(body,"type")!="triSurfaceMesh":raise ValueError("unsupported geometry producer")
        allowed_entries(body,{"type","name","regions"})
        for rb in blocks(blocks(body).get("regions","")).values():allowed_entries(rb,{"name"})
        q=directory/filename
        if q.is_symlink():raise ValueError("actual STL symlink")
        data=q.read_bytes();actual=hashlib.sha256(data).hexdigest()
        names=re.findall(r"(?m)^solid[ \t]+([^\r\n]+)",data.decode())
        if not names or len(set(names))!=len(names):raise ValueError("duplicate/missing actual STL solid")
        if filename in bypath and actual==orig[bypath[filename]]["contentHash"]:refs=[bypath[filename]];op="literal"
        elif filename=="merged_floor_ceiling.stl"and filename in bypath:refs=[bypath[filename]];op="roomEnclosure"
        elif filename=="merged_wall_baffle.stl"and"merged_baffle.stl"in bypath:refs=[bypath["merged_baffle.stl"]];op="baffleRegroup"
        elif filename.startswith("wallBaffle_floor_")and"merged_floor_ceiling.stl"in bypath:refs=[bypath["merged_floor_ceiling.stl"]];op="baffleSplit"
        else:raise ValueError("unknown actual STL transformation")
        files[filename]=dict(sha256=actual,solidNames=sorted(names),sourceFileIds=refs,operation=op)
        alias=scalar(body,"name");sb=surfs[alias];sbblocks=blocks(sb)
        if scalar(sb,"faceType")=="internal":
            zone=scalar(sb,"faceZone")
            if not zone:raise ValueError("unknown internal zone interface")
            for side in["master","slave"]:
                patch=zone+"_"+side
                if patch not in registry_raw:raise ValueError("internal sheet has no resolved binding")
                regions.append(dict(file=filename,solidNames=sorted(names),patchName=patch,patchType="wall"if zone in wall_pair_zones else"cyclic",bcType=registry_raw[patch],origin="resolved",sourceLineage=[source_lineage(raw,k,n,op)for k in refs for n in sorted(names)]))
        else:
            regionblocks=blocks(blocks(body).get("regions",""));refblocks=blocks(sbblocks.get("regions",""))
            if set(regionblocks)!=set(names):raise ValueError("STL/dictionary named region mismatch")
            for solid in sorted(names):
                patch=scalar(regionblocks[solid],"name");rb=refblocks.get(solid,sb);pb=blocks(rb).get("patchInfo")or sbblocks.get("patchInfo","")
                typ=scalar(pb,"type")
                if typ is None:raise ValueError("missing explicit patchInfo type")
                bc=registry_raw.get(patch,"wall");origin="resolved"if patch in registry_raw else"generatedWall"
                if origin=="generatedWall"and typ!="wall":raise ValueError("unresolved non-wall actual region")
                regions.append(dict(file=filename,solidNames=[solid],patchName=patch,patchType=typ,bcType=bc,origin=origin,sourceLineage=[source_lineage(raw,k,solid,op)for k in refs]))
    aliases={scalar(body,"name")for key,body in geom.items()if key.endswith(".stl")}
    if set(surfs)!=aliases:raise ValueError("unexplained refinement surface")
    if set(refinement)!=set(refinement_only):raise ValueError("unexplained refinement region")
    # Exact source-surface metadata transported into the native dictionary.
    source_blocks=blocks(top.get("dcdtuniSourceSurfaces",""));source_metadata={}
    for filename,body in source_blocks.items():
        if filename not in files:raise ValueError("source metadata outside actual inventory")
        expected_file_ids=files[filename]["sourceFileIds"]
        mapped={}
        for solid,row in blocks(body).items():
            allowed_entries(row,{"sourceObjectUid","sourceFaceUid","representation"})
            actual={k:scalar(row,k)for k in ["sourceObjectUid","sourceFaceUid","representation"]}
            expected=[{k:v.get(k)for k in actual}for v in(raw["geometryExport"].get("sourceSurfaces",{})).values()if v.get("stlFileId")in expected_file_ids and v.get("solidName")==solid]
            if not expected or any(v!=actual for v in expected):raise ValueError("source surface transport mismatch")
            mapped[solid]=actual
        source_metadata[filename]=mapped
    # The production exclusion predicate is shared by runner and acceptance.
    # These immutable editor-only export buckets have no mesh patch or BC.
    from backend.aircooling.runner.mesh_acceptance import is_editor_only_stl
    for q in sorted(directory.glob("*.stl")):
        if q.name in files:continue
        if not is_editor_only_stl(q.name) or q.name not in bypath or q.is_symlink():raise ValueError("unexplained non-meshed STL")
        data=q.read_bytes();key=bypath[q.name]
        if hashlib.sha256(data).hexdigest()!=orig[key]["contentHash"]:raise ValueError("editor-only STL identity mismatch")
        names=re.findall(r"(?m)^solid[ \t]+([^\r\n]+)",data.decode())
        files[q.name]=dict(sha256=hashlib.sha256(data).hexdigest(),solidNames=sorted(names),sourceFileIds=[key],operation="literal",usage="editorOnly")
    return dict(files=files,regions=regions,refinementOnly=refinement_only,sourceSurfaceMetadata=source_metadata,verifiedOriginal=verified_original_receipt,dictionarySHA256=hashlib.sha256(snappy_bytes).hexdigest())
def per_run_contract(raw_bytes,*,locked_raw_sha256,derived_data,resolved_run,tri_surface_dir,snappy_bytes,wall_pair_zones,verified_original_receipt):
    """Normal writer entry. Missing provenance or unsupported input disables only singleton."""
    try:
        raw=json.loads(raw_bytes)
        if not isinstance(raw,dict):raise ValueError("invalidRawShape")
        if not isinstance(verified_original_receipt,dict):
            raise ValueError("missingOriginalPreflightReceipt")
        inv=collect_actual_inventory(raw,tri_surface_dir=tri_surface_dir,snappy_bytes=snappy_bytes,wall_pair_zones=wall_pair_zones,verified_original_receipt=verified_original_receipt)
        return derive_singleton_authority(raw_bytes,locked_raw_sha256=locked_raw_sha256,derived_data=derived_data,resolved_run=resolved_run,actual_region_inventory=inv)
    except (ValueError,TypeError,KeyError,OSError) as exc:
        rawsha=hashlib.sha256(raw_bytes).hexdigest()
        return dict(version=1,eligible=False,rawSha256=rawsha,sourceSHA256=rawsha,rolesDigest=digest({}),scope=SCOPE,reasons=["inventoryUnavailable_"+type(exc).__name__])
def foam_contract(out):
    reasons=" ".join(re.sub("[^A-Za-z0-9_]","_",s)for s in out["reasons"])
    return 'dcdtuniPointRepairSourceRoleContract\n{\n    version 1;\n    eligible '+("true"if out["eligible"]else"false")+';\n    rawSha256 "'+out["rawSha256"]+'";\n    rolesDigest "'+out["rolesDigest"]+'";\n    scope '+SCOPE+';\n    reasons ('+reasons+');\n}\n'
