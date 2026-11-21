"use strict";
// TOOL-ONLY independent implementation of the audited disk-project fingerprint recipe.
// Does not import product canonicalization, graph, hash, React, generator or solver code.
const crypto = require("node:crypto");
function locateLeaf(root, pieces, targetValues, at=0, valueAt=0) {
  if (root === null || typeof root !== "object") return null;
  const part = pieces[at];
  if (at === pieces.length - 1) {
    if (!/^\w+$/.test(part)) throw new Error("unsupported fingerprint registry leaf");
    return [root, part];
  }
  if (part === "*") {
    for (const key of Object.keys(root)) {
      const hit = locateLeaf(root[key], pieces, targetValues, at + 1, valueAt);
      if (hit) return hit;
    }
    return null;
  }
  if (/^\{\w+\}$/.test(part)) {
    const key = targetValues[valueAt];
    const value = Array.isArray(root) ? root[Number(key)] : root[key];
    return locateLeaf(value, pieces, targetValues, at + 1, valueAt + 1);
  }
  const match = /^(\w+)\[id=\{\w+\}\]$/.exec(part);
  if (match) {
    const list = Array.isArray(root[match[1]]) ? root[match[1]] : [];
    const chosen = list.find(row => row && String(row.id) === targetValues[valueAt]);
    return locateLeaf(chosen, pieces, targetValues, at + 1, valueAt + 1);
  }
  if (!/^\w+$/.test(part)) throw new Error("unsupported fingerprint registry segment");
  return locateLeaf(root[part], pieces, targetValues, at + 1, valueAt);
}
function canonicalDiskProject(project, registry) {
  const copy = JSON.parse(JSON.stringify(project));
  const tracks = copy.transient && copy.transient.tracks;
  if (!Array.isArray(tracks) || !tracks.length) return copy;
  if (!registry || !Array.isArray(registry.params)) throw new Error("fingerprint requires audited registry");
  const models = new Map();
  for (const items of Object.values(copy.constructions || {})) {
    if (items === null || typeof items !== "object") continue;
    for (const [uid, model] of Object.entries(items)) if (model && typeof model === "object") models.set(uid, model);
  }
  for (const track of tracks) {
    const model = models.get(track.modelUid);
    const entry = registry.params.find(row => row.key === track.param);
    if (!model || !entry || typeof entry.path !== "string") throw new Error("unresolved tracked fingerprint field");
    const target = track.target === "" ? [] : String(track.target).split("/");
    const leaf = locateLeaf(model, entry.path.split("."), target);
    if (leaf) delete leaf[0][leaf[1]];
  }
  return copy;
}
function sortOwnJSON(value) {
  if (Array.isArray(value)) return value.map(sortOwnJSON);
  if (value && typeof value === "object") {
    const result = Object.create(null);
    for (const key of Object.keys(value).sort()) result[key] = sortOwnJSON(value[key]);
    return result;
  }
  return value;
}
function independentProjectFingerprint(project, registry) {
  const text = JSON.stringify(sortOwnJSON(canonicalDiskProject(project, registry)));
  return crypto.createHash("sha256").update(text, "utf8").digest("hex");
}
exports.independentProjectFingerprint = independentProjectFingerprint;
