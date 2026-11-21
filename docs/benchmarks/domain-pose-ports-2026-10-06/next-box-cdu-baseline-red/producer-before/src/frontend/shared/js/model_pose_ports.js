/**
 * Numeric model poses and equipment-port frames in persisted project coordinates.
 * This kernel owns the legacy arithmetic. Renderers only provide vector carriers;
 * physical adapters continue to own any conversion from project units to metres.
 */
(function (root, factory) {
    var api = Object.freeze(factory());
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    } else if (root) {
        root.modelPosePorts = api;
        if (typeof window !== 'undefined' && window !== root) window.modelPosePorts = api;
    }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    function getDefaultModelTransform() {
        return {
            quaternion: { x: 0, y: 0, z: 0, w: 1 },
            scale: { x: 1, y: 1, z: 1 }
        };
    }

    function cloneModelTransform(rawTransform = {}) {
        const fallback = getDefaultModelTransform();
        const quaternion = rawTransform.quaternion || {};
        const scale = rawTransform.scale || {};
        return {
            quaternion: {
                x: Number.isFinite(quaternion.x) ? quaternion.x : fallback.quaternion.x,
                y: Number.isFinite(quaternion.y) ? quaternion.y : fallback.quaternion.y,
                z: Number.isFinite(quaternion.z) ? quaternion.z : fallback.quaternion.z,
                w: Number.isFinite(quaternion.w) ? quaternion.w : fallback.quaternion.w
            },
            scale: {
                x: Number.isFinite(scale.x) ? scale.x : fallback.scale.x,
                y: Number.isFinite(scale.y) ? scale.y : fallback.scale.y,
                z: Number.isFinite(scale.z) ? scale.z : fallback.scale.z
            }
        };
    }

    function modelWorldTransform(model) {
        const g = (model && model.geometry) ? model.geometry : {};
        return cloneModelTransform({ quaternion: g.quaternion, scale: g.scale });
    }

    // Preserve Quaternion.normalize's sqrt, reciprocal and component order.
    function normalizeQuaternion(q) {
        let length = Math.sqrt(q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w);
        if (length === 0) return { x: 0, y: 0, z: 0, w: 1 };
        length = 1 / length;
        return { x: q.x * length, y: q.y * length, z: q.z * length, w: q.w * length };
    }

    function modelPose(model) {
        const g = (model && model.geometry) ? model.geometry : {};
        const anchor = g.location || { x: 0, y: 0, z: 0 };
        const transform = modelWorldTransform(model);
        return {
            position: { x: Number(anchor.x) || 0, y: Number(anchor.y) || 0, z: Number(anchor.z) || 0 },
            quaternion: normalizeQuaternion(transform.quaternion),
            scale: transform.scale
        };
    }

    // Preserve Vector3.applyQuaternion's cross-product expansion, not a matrix.
    function applyQuaternion(v, q) {
        const vx = v.x, vy = v.y, vz = v.z;
        const qx = q.x, qy = q.y, qz = q.z, qw = q.w;
        const tx = 2 * (qy * vz - qz * vy);
        const ty = 2 * (qz * vx - qx * vz);
        const tz = 2 * (qx * vy - qy * vx);
        return {
            x: vx + qw * tx + qy * tz - qz * ty,
            y: vy + qw * ty + qz * tx - qx * tz,
            z: vz + qw * tz + qx * ty - qy * tx
        };
    }

    function scaledVector(value, scale) {
        return {
            x: (Number(value.x) || 0) * scale.x,
            y: (Number(value.y) || 0) * scale.y,
            z: (Number(value.z) || 0) * scale.z
        };
    }

    function modelLocalPointToWorld(model, point) {
        if (!model || !point) return null;
        const pose = modelPose(model);
        const v = applyQuaternion(scaledVector(point, pose.scale), pose.quaternion);
        return { x: v.x + pose.position.x, y: v.y + pose.position.y, z: v.z + pose.position.z };
    }

    function modelLocalDirectionToWorld(model, direction) {
        if (!model || !direction) return null;
        const transform = modelWorldTransform(model);
        const q = normalizeQuaternion(transform.quaternion);
        const v = applyQuaternion(scaledVector(direction, transform.scale), q);
        const reciprocal = 1 / (Math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z) || 1);
        return { x: v.x * reciprocal, y: v.y * reciprocal, z: v.z * reciprocal };
    }

    function rackManifoldLocalPoint(model, portType, end) {
        const g = model.geometry || {};
        const size = g.size || {};
        const cooling = model.cooling || {};
        const mf = cooling.liquidManifold || {};
        const supply = mf.supplyPipe || {};
        const ret = mf.returnPipe || {};
        const role = String(portType || '').toLowerCase();
        const isSupply = role.indexOf('supply') >= 0 || role.indexOf('inlet') >= 0 || role === 'manifold_in';
        const cfg = isSupply ? supply : ret;
        const diameter = Number.isFinite(cfg.diameter) ? cfg.diameter : 0.025;
        const radius = diameter / 2;
        const spacing = Number.isFinite(mf.pipeSpacing) ? mf.pipeSpacing : 0.015;
        const edge = 0.01;
        const width = Number(size.x) || 0;
        let x;
        if (mf.layout === 'both-right') {
            const returnRadius = (Number.isFinite(ret.diameter) ? ret.diameter : 0.025) / 2;
            const returnX = width - edge - returnRadius;
            x = isSupply ? returnX - returnRadius - spacing - radius : returnX;
        } else if ((mf.layout || 'both-left') === 'both-left') {
            const supplyRadius = (Number.isFinite(supply.diameter) ? supply.diameter : 0.025) / 2;
            const supplyX = edge + supplyRadius;
            x = isSupply ? supplyX : supplyX + supplyRadius + spacing + radius;
        } else {
            x = isSupply ? edge + radius : width - edge - radius;
        }
        const safeBottom = Number(model.safeMarginBottom) || 0;
        const slotHeight = (Number(g.slot) || 0) * (Number(g.slotHeight) || 0);
        const usableTop = slotHeight > 1e-6
            ? safeBottom + slotHeight
            : Math.max(safeBottom, (Number(size.y) || 0) * 0.9);
        const y = end === 'bottom' ? safeBottom : usableTop;
        const z = mf.mountFace === 'outlet' ? (Number(size.z) || 0) + 0.05 : -0.05;
        return { x: x, y: y, z: z };
    }

    function equipmentPortFrame(model, kind, portType, end) {
        if (!model || !model.geometry) return null;
        const size = model.geometry.size || {};
        let localPosition;
        let localDirection;
        if (kind === 'cdu') {
            const role = String(portType || '').toLowerCase();
            const isSupply = role.indexOf('supply') >= 0 || role.indexOf('inlet') >= 0;
            localPosition = {
                x: (Number(size.x) || 0) / 2,
                y: (Number(size.y) || 0) * 0.85 - (isSupply ? 0 : 0.05),
                z: Number(size.z) || 0
            };
            localDirection = { x: 0, y: 0, z: 1 };
        } else if (kind === 'rack') {
            localPosition = rackManifoldLocalPoint(model, portType, end || 'top');
            localDirection = { x: 0, y: 1, z: 0 };
        } else {
            return null;
        }
        return {
            position: modelLocalPointToWorld(model, localPosition),
            direction: modelLocalDirectionToWorld(model, localDirection),
            localPosition: localPosition,
            localDirection: localDirection
        };
    }

    return {
        getDefaultModelTransform: getDefaultModelTransform,
        cloneModelTransform: cloneModelTransform,
        modelWorldTransform: modelWorldTransform,
        modelPose: modelPose,
        modelLocalPointToWorld: modelLocalPointToWorld,
        modelLocalDirectionToWorld: modelLocalDirectionToWorld,
        equipmentPortFrame: equipmentPortFrame
    };
});
