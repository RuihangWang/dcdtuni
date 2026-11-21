/**
 * Which feature-tree sub-row is lit for the current narrow selection.
 *
 * The legacy tree builder decides this per row type (STL_generate.html
 * `getTreeHtmlForModel`: `isFaceTreeHighlighted`,
 * `isCustomRegionTreeHighlighted` and the inline `selected` expressions of the
 * rack-slot, server, server-face, server-patch, partition, wall-region and
 * solid-region rows). React drew every sub-row as a plain `node-label`, so a
 * server, a server face or a face picked in the viewport or in the tree itself
 * was never lit — only the model row and its category were.
 *
 * The rows already carry what they select (`TreeChild.select`), so the rule is
 * a comparison of that target with the current node, kept pure so the table can
 * be pinned without a DOM. Mirrors the legacy exactly, including where it is
 * narrower than one might guess: a server PATCH lights the server row and the
 * patch row but not the server-face row between them, and a rack's face row is
 * lit by a U-slot or a whole server but not by one of the server's faces.
 */

import type { TreeChild, TreeSelection } from './treeNodes'

/** The slots a rackSlot selection covers: its `indices`, or its one `index`. */
function slotIndices(current: TreeSelection): number[] {
  if (Array.isArray(current.indices)) return current.indices
  return typeof current.index === 'number' ? [current.index] : []
}

/** The legacy `getCustomRegionKey`: an unnamed region is the whole model. */
function regionKey(name: string | undefined): string {
  return name || '__whole__'
}

/**
 * Is the row that selects `select` lit while `current` is selected?
 *
 * `children` are the row's own children, for the two rows whose highlight
 * depends on what hangs under them: a multi-U server under a rack face (lit by
 * a slot selection touching its U range) and a custom-model region (lit by a
 * patch listed under it).
 */
export function isTreeNodeHighlighted(
  select: TreeSelection | undefined,
  current: TreeSelection | null | undefined,
  children?: readonly TreeChild[],
): boolean {
  if (!select || !current || current.uid !== select.uid) return false
  switch (select.type) {
    case 'face':
      if (current.type === 'face' || current.type === 'patch' || current.type === 'wallRegion') {
        return current.faceId === select.faceId
      }
      if (current.type === 'rackSlot') return current.side === select.faceId
      if (current.type === 'equipment') {
        // A server picked from one side lights that rack face; picked with no
        // side (its row under Equipments) it lights both.
        return current.side
          ? current.side === select.faceId
          : select.faceId === 'front' || select.faceId === 'back'
      }
      return false

    case 'patch':
      return current.type === 'patch' && current.patchId === select.patchId

    case 'wallRegion':
      return (
        current.type === 'wallRegion' &&
        current.faceId === select.faceId &&
        current.index === select.index
      )

    case 'equipment':
      if (select.side) {
        // The multi-U server row under a rack face.
        if (current.type === 'equipment') return current.equipmentId === select.equipmentId
        if (current.type === 'rackSlot' && current.side === select.side) {
          const picked = slotIndices(current)
          return (children ?? []).some(
            (child) =>
              child.select?.type === 'rackSlot' &&
              typeof child.select.index === 'number' &&
              picked.includes(child.select.index),
          )
        }
        return false
      }
      // The server row under Equipments.
      return (
        (current.type === 'equipment' ||
          current.type === 'serverFace' ||
          current.type === 'serverPatch') &&
        current.equipmentId === select.equipmentId
      )

    case 'serverFace':
      return (
        current.type === 'serverFace' &&
        current.equipmentId === select.equipmentId &&
        current.faceId === select.faceId
      )

    case 'serverPatch':
      return (
        current.type === 'serverPatch' &&
        current.equipmentId === select.equipmentId &&
        current.patchId === select.patchId
      )

    case 'rackSlot':
      return (
        current.type === 'rackSlot' &&
        current.side === select.side &&
        typeof select.index === 'number' &&
        slotIndices(current).includes(select.index)
      )

    case 'partition':
      return current.type === 'partition' && current.index === select.index

    case 'region':
      if (current.type === 'region') return regionKey(current.regionName) === regionKey(select.regionName)
      if (current.type === 'patch') {
        return (children ?? []).some(
          (child) => child.select?.type === 'patch' && child.select.patchId === current.patchId,
        )
      }
      return false

    default:
      // 'model' (a pipe's joined-with row) and 'fitting': the legacy builder
      // lights neither.
      return false
  }
}
