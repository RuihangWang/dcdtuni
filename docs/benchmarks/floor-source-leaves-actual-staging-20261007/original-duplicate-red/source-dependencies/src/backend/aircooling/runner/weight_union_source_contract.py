"""Isolated preview. Normal current producer has no fluid-domain declaration.
The future domain models below are design fixtures, not required for the
current globally single-material model; native final-fluid ownership is separate.
No absence flag, zone emptiness, component count or IO region grants ownership.
"""
from __future__ import annotations
import hashlib
import json
import math
import re
from fractions import Fraction
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from backend.aircooling.runner import point_repair_source_contract as singleton

SCOPE = "globalSinglePhaseAirMaterial"
HEX = re.compile(r"[0-9a-f]{64}")
EMPTY_DIGEST = hashlib.sha256(b"{}").hexdigest()

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

class StrictProducerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

class FluidProperties(StrictProducerModel):
    densityKgM3: float
    kinematicViscosityM2S: float
    specificHeatJKgK: float
    conductivityWMK: float
    @model_validator(mode="after")
    def finite_positive(self):
        for v in self.model_dump().values():
            if not math.isfinite(v) or v <= 0:
                raise ValueError("finite positive fluid property required")
        return self

class BoundaryDomainMember(StrictProducerModel):
    # Digest of a complete actual inventory region row, including resolved
    # patch/BC type and original/derived source lineage; no bare patch alias.
    regionDigest: str
    domainUid: str
    @field_validator("regionDigest")
    @classmethod
    def sha(cls, v):
        if not HEX.fullmatch(v): raise ValueError("lowercase SHA256 required")
        return v

class DeclaredFluidDomain(StrictProducerModel):
    uid: str
    owningVolumeUid: str
    volumeClassifier: Literal["closedRoomFluidMinusDeclaredObstacles"]
    materialUid: str
    materialModel: Literal["incompressibleNewtonianConstantProperties"]
    properties: FluidProperties
    componentPolicy: Literal["allClassifiedComponentsShareDeclaredMaterial"]
    @field_validator("uid", "owningVolumeUid", "materialUid")
    @classmethod
    def explicit_uid(cls, value):
        if not value or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", value):
            raise ValueError("explicit producer UID required")
        return value

class FluidDomainExport(StrictProducerModel):
    """Proposed typed export increment. Current CfdrunV2 does NOT contain it."""
    version: Literal[1]
    fluidDomains: list[DeclaredFluidDomain]
    boundaryDomains: list[BoundaryDomainMember]
    # Explicit producer closure, also independently checked by the old typed
    # absence proof; these required arrays cannot be inferred from empty zones.
    materialDomains: list[dict]
    bulkSourceDomains: list[dict]
    internalInterfaceDomains: list[dict]

def verify_producer_fixture(export, actual_inventory, necessary_absence):
    """Future design validator only, independently binds fresh inventory roles."""
    reasons = []
    try: typed = FluidDomainExport.model_validate(export)
    except (ValueError, TypeError): return dict(valid=False, reasons=["invalidTypedDomainDeclaration"])
    if not necessary_absence.get("eligible"): reasons.append("necessaryAbsenceProofRefused")
    if len(typed.fluidDomains) != 1: reasons.append("requiresExactlyOneDeclaredFluidDomain")
    if typed.materialDomains or typed.bulkSourceDomains or typed.internalInterfaceDomains:
        reasons.append("materialBulkOrInterfaceDomainPresent")
    expected = {digest(row) for row in actual_inventory.get("regions", [])}
    declared = [row.regionDigest for row in typed.boundaryDomains]
    if not expected or len(declared) != len(set(declared)) or set(declared) != expected:
        reasons.append("incompleteBoundaryDomainMembership")
    uid = typed.fluidDomains[0].uid if len(typed.fluidDomains) == 1 else ""
    if any(row.domainUid != uid for row in typed.boundaryDomains):
        reasons.append("boundaryMemberUnknownOrDifferentDomain")
    domain = typed.fluidDomains[0].model_dump() if uid else {}
    return dict(valid=not reasons, reasons=reasons, domainUid=uid,
                materialDigest=digest(domain.get("properties", {})),
                declarationDigest=digest(typed.model_dump()),
                boundaryMembershipDigest=digest([r.model_dump() for r in typed.boundaryDomains]))

def _refusal(raw_bytes, absence, reason):
    return dict(version=1, eligible=False, scope=SCOPE,
                rawSha256=hashlib.sha256(raw_bytes).hexdigest(),
                rolesDigest=absence.get("rolesDigest", EMPTY_DIGEST),
                domainUid="", materialDigest=EMPTY_DIGEST,
                declarationDigest=EMPTY_DIGEST, boundaryMembershipDigest=EMPTY_DIGEST,
                producerDigest=EMPTY_DIGEST,materialKey="",
                ownershipPolicy="adjacentFinalCarvedFluidCells",reasons=[reason])

def derive_weight_authority(raw_bytes, *, locked_raw_sha256, derived_data,
                            resolved_run, actual_region_inventory, necessary_absence=None):
    absence = necessary_absence if necessary_absence is not None else singleton.derive_singleton_authority(
        raw_bytes, locked_raw_sha256=locked_raw_sha256, derived_data=derived_data,
        resolved_run=resolved_run, actual_region_inventory=actual_region_inventory)
    out = _refusal(raw_bytes, absence, "missingNormalMaterialProducer")
    out.update(materialKey="", producerDigest=EMPTY_DIGEST,
               ownershipPolicy="adjacentFinalCarvedFluidCells")
    if out["rawSha256"] != locked_raw_sha256 or not HEX.fullmatch(str(locked_raw_sha256)):
        out["reasons"] = ["lockedRawIdentityMismatch"]; return out
    if not absence.get("eligible"):
        out["reasons"] = ["necessarySourceRoleProofRefused"]; return out
    try:
        import os
        from pathlib import Path
        from backend.aircooling.runner import openfoam_case
        from backend.aircooling.utils import const
        # Arbitrary binary/model overrides are not the reviewed normal producer.
        if os.environ.get("DCDTUNI_SOLVER_OVERRIDE"):
            raise ValueError("solverOverrideUnsupported")
        resolver = getattr(openfoam_case, "resolve_global_air_material", None)
        if resolver is None: raise ValueError("missingNormalMaterialProducer")
        sc = dict(derived_data.get("simulationConfig", {}))
        # Preserve the existing normal runner call's legacy fallback exactly.
        sc["roomTemperature"] = float(sc.get("roomTemperature", None) or 22.0)
        material = resolver(sc)
        if not all(math.isfinite(v) for v in material.values() if isinstance(v,float)):
            raise ValueError("nonfiniteMaterial")
        producer = {"openfoamCaseSha256":hashlib.sha256(Path(openfoam_case.__file__).read_bytes()).hexdigest(),
                    "airConstantsSha256":hashlib.sha256(Path(const.__file__).read_bytes()).hexdigest()}
    except (ValueError,TypeError,KeyError,OSError) as exc:
        out["reasons"] = ["materialProducerRefused_"+str(exc)]; return out
    out.update(eligible=True, reasons=[], material=material,
        materialDigest=digest(material), materialKey="global-air:"+digest(material),
        producerDigest=digest(producer), producer=producer,
        boundaryMembershipDigest=digest(actual_region_inventory["regions"]))
    # This proves one global material only. Native whole-parent live-fluid
    # ownership/shape/facet callback is still independently required by APP.
    return out

def per_run_contracts(raw_bytes, *, locked_raw_sha256, derived_data, resolved_run,
                      tri_surface_dir, snappy_bytes, wall_pair_zones, verified_original_receipt):
    """One real inventory read, independent two authorities, fresh on every run."""
    try:
        raw = json.loads(raw_bytes)
        if not isinstance(raw, dict) or not isinstance(verified_original_receipt, dict):
            raise ValueError("missingOriginalPreflightReceipt")
        inv = singleton.collect_actual_inventory(
            raw, tri_surface_dir=tri_surface_dir, snappy_bytes=snappy_bytes,
            wall_pair_zones=wall_pair_zones, verified_original_receipt=verified_original_receipt)
        absence = singleton.derive_singleton_authority(
            raw_bytes, locked_raw_sha256=locked_raw_sha256, derived_data=derived_data,
            resolved_run=resolved_run, actual_region_inventory=inv)
        weight = derive_weight_authority(
            raw_bytes, locked_raw_sha256=locked_raw_sha256, derived_data=derived_data,
            resolved_run=resolved_run, actual_region_inventory=inv, necessary_absence=absence)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        rawsha = hashlib.sha256(raw_bytes).hexdigest()
        absence = dict(version=1, eligible=False, rawSha256=rawsha, sourceSHA256=rawsha,
            rolesDigest=EMPTY_DIGEST, scope=singleton.SCOPE, reasons=["inventoryUnavailable_"+type(exc).__name__])
        weight = _refusal(raw_bytes, absence, "inventoryUnavailable_"+type(exc).__name__)
    return dict(singleton=absence, weight=weight)

def invalidate(contracts, reason):
    for authority in contracts.values():
        authority["eligible"] = False
        authority["reasons"] = sorted(set(authority["reasons"]+[reason]))

def foam_weight_contract(out):
    if out.get("scope") != SCOPE: raise ValueError("unsupported material contract scope")
    for key in ("rawSha256","rolesDigest","materialDigest","producerDigest","boundaryMembershipDigest"):
        if not HEX.fullmatch(str(out.get(key, ""))): raise ValueError("invalid contract digest")
    if out.get("eligible") and (out.get("reasons") or not out.get("materialKey")):
        raise ValueError("malformed eligible material proof")
    reasons = " ".join(re.sub("[^A-Za-z0-9_]", "_", x) for x in out["reasons"])
    return ("dcdtuniWeightUnionSourceContract\n{\n    version 1;\n    eligible "
        +("true" if out.get("eligible") else "false")+";\n"
        '    rawSha256 "'+out["rawSha256"]+'";\n    rolesDigest "'+out["rolesDigest"]+'";\n'
        "    scope "+SCOPE+';\n    materialKey "'+out.get("materialKey","")+'";\n'
        '    materialDigest "'+out["materialDigest"]+'";\n'
        '    producerDigest "'+out["producerDigest"]+'";\n'
        '    boundaryMembershipDigest "'+out["boundaryMembershipDigest"]+'";\n'
        "    ownershipPolicy adjacentFinalCarvedFluidCells;\n    reasons ("+reasons+");\n}\n")

def _shape_proof(points, facets):
    # Future normal builder fixture contract: ordered whole source parent shell,
    # DP finite coordinates, every edge exactly twice in opposite directions.
    if not points or any(len(p)!=3 or any(not math.isfinite(x) for x in p) for p in points):
        return False
    uses = {}
    for f in facets:
        if len(f)<3 or len(set(f))!=len(f) or any(type(i)!=int or i<0 or i>=len(points) for i in f):
            return False
        for a,b in zip(f,f[1:]+f[:1]): uses[(a,b)] = uses.get((a,b),0)+1
    if not uses or any(n!=1 or uses.get((b,a),0)!=1 for (a,b),n in uses.items()): return False
    # Signed volume by exact binary-rational tetrahedral divergence.
    q = [[Fraction(x) for x in p] for p in points]
    v6 = Fraction(0)
    for f in facets:
        a=q[f[0]]
        for i in range(1,len(f)-1):
            b,c=q[f[i]],q[f[i+1]]
            v6 += a[0]*(b[1]*c[2]-b[2]*c[1])+a[1]*(b[2]*c[0]-b[0]*c[2])+a[2]*(b[0]*c[1]-b[1]*c[0])
    return v6 > 0

def verify_origin_pair_fixture(origins, normal_parent_records, producer_proof):
    """Future normal mapping design fixture; NOT native eligibility today."""
    if not producer_proof.get("valid") or len(origins)!=2 or origins[0]==origins[1]: return False
    for origin in origins:
        required={"nativeParent","rank","level","leaf","piece","region","sourceFacets"}
        if set(origin)!=required: return False
        identity=tuple(origin[k] for k in ("rank","nativeParent","level","leaf","piece"))
        record=normal_parent_records.get(identity)
        if not record or not record.get("classificationVerified") or not record.get("sameHomogeneousFluid"):
            return False
        if record.get("domainUid")!=producer_proof["domainUid"] or record.get("materialDigest")!=producer_proof["materialDigest"]:
            return False
        if origin["region"] != record.get("region") or origin["sourceFacets"] != record.get("sourceFacets"):
            return False
        if any(type(x)!=int or x<0 for x in identity) or record.get("cellZone") != -1:
            return False
        facets,points=record.get("orderedFacets",[]),record.get("points",[])
        if not _shape_proof(points,facets) or len(origin["sourceFacets"])!=len(facets):
            return False
        if any(len(row)!=3 or row[2]!=-1 for row in origin["sourceFacets"]): return False
        if record.get("shapeDigest") != digest({"points":points,"orderedFacets":facets}): return False
    return True
