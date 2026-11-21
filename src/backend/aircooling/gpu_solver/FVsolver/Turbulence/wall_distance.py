"""Distance of the cell centres to the wall surface, for the RANS models of ``rhocoviscompt``.

The wall is the union of its faces, each face the fan of triangles from its centre to its edges, and a
point's distance is its exact distance to the nearest triangle (SU2 measures to its surface elements the
same way).  Searching: for every point the faces are ranked by the lower bound |x - x_f| - R_f
(R_f the largest node distance from the face centre), the exact distance is taken over the 16 faces of
smallest bound, then over 256 and then over all faces for the points whose 16th / 256th bound is still
below (1 - 1e-3) times the distance found: near the wall the result is exact, far from it the relative
error is below 1e-3.

Why not the nearest face centre.  The distance to the nearest wall face centre, corrected by the
tangential offset beyond half the spacing to the second-nearest centre (``rhocoviscompt`` before
2026-10-15), is wrong wherever the nearest centre does not belong to the face the point sits on:
on the sharp trailing edges of the RAE2822 and NACA0012 O-grids it gave the first fan cells 1.6e-4
and 5.8e-5 instead of 6.7e-7 (240 and 86 times too far), and on the ONERA M6 grid it put 831 cells
along the trailing edge more than 10 times too far (2.6e-3 instead of 3.9e-7).  SA's destruction
(nut / d)^2 is then 1e4-1e7 times too weak there: on the M6 grid nut grew in the wall cells under the
trailing edge near the tip until the explicit and the defect-corrected marches diverged (2026-10-15).

Source: copied unchanged from Gen-FVGN-3D origin/dev/odil 527bf4a9a4a1ebd8ee6d6826785f4cab5eacfb5e, src/FVsolver/Turbulence/wall_distance.py.
"""
import torch


def _segment_dist2(x: torch.Tensor, a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    ab = b - a
    l2 = (ab * ab).sum(-1)
    t = ((x - a) * ab).sum(-1) / l2.clamp_min(1e-300)
    t = torch.where(l2 > 0, t.clamp(0.0, 1.0), torch.zeros_like(t))
    r = x - a - t.unsqueeze(-1) * ab
    return (r * r).sum(-1)


def _triangle_dist2(x: torch.Tensor, a: torch.Tensor, b: torch.Tensor, c: torch.Tensor) -> torch.Tensor:
    """Squared distance of x to the triangle (a, b, c); a degenerate triangle counts by its edges."""
    n = torch.cross(b - a, c - a, dim=-1)
    nn = (n * n).sum(-1)
    flat = nn > 1e-24 * ((b - a) ** 2).sum(-1) * ((c - a) ** 2).sum(-1)
    nh = n / nn.clamp_min(1e-300).sqrt().unsqueeze(-1)
    h = ((x - a) * nh).sum(-1)
    xp = x - h.unsqueeze(-1) * nh
    inside = flat
    for p, q in ((a, b), (b, c), (c, a)):
        inside = inside & ((torch.cross(q - p, xp - p, dim=-1) * n).sum(-1) >= 0)
    edges = torch.minimum(torch.minimum(_segment_dist2(x, a, b), _segment_dist2(x, b, c)), _segment_dist2(x, c, a))
    return torch.where(inside, h * h, edges)


def wall_fan_triangles(face_node: torch.Tensor, face_node_ptr: torch.Tensor, faces: torch.Tensor,
                       node_pos: torch.Tensor, face_pos: torch.Tensor):
    """Fan triangles (face_pos[j], b[j, i], c[j, i]) of the faces ``faces``, their nodes in stored order.

    ``face_node`` lists the nodes of all faces, ``face_node_ptr`` the face of each entry; ``face_pos``
    holds the centres of ``faces``.  Faces with fewer nodes than the largest are padded with the
    degenerate triangle (centre, node 0, node 0).  Returns b, c [W, M, 3] and the radius R [W]."""
    sel = torch.isin(face_node_ptr, faces)
    fn, fp = face_node[sel], face_node_ptr[sel]
    order = torch.argsort(fp, stable=True)
    fn, fp = fn[order], fp[order]
    W, dev = faces.numel(), fn.device
    loc = torch.full((int(face_node_ptr.max()) + 1,), -1, dtype=torch.long, device=dev)
    loc[faces] = torch.arange(W, device=dev)
    j = loc[fp]
    cnt = torch.bincount(j, minlength=W)
    start = torch.zeros(W, dtype=torch.long, device=dev)
    start[1:] = torch.cumsum(cnt, 0)[:-1]
    M = int(cnt.max())
    tab = fn[start].unsqueeze(1).repeat(1, M)
    tab[j, torch.arange(fn.numel(), device=dev) - start[j]] = fn
    nxt = torch.arange(M, device=dev).unsqueeze(0) + 1
    nxt = torch.where(nxt < cnt.unsqueeze(1), nxt, torch.zeros_like(nxt))
    b, c = node_pos[tab], node_pos[torch.gather(tab, 1, nxt)]
    pad = torch.arange(M, device=dev).unsqueeze(0) >= cnt.unsqueeze(1)
    c = torch.where(pad.unsqueeze(-1), b, c)
    radius = (b - face_pos.unsqueeze(1)).norm(dim=-1).max(1)[0]
    return b, c, radius


def surface_distance(x: torch.Tensor, face_pos: torch.Tensor, b: torch.Tensor, c: torch.Tensor,
                     radius: torch.Tensor, rtol: float = 1e-3, budget: int = 1 << 25) -> torch.Tensor:
    """Distance of the points x [n, 3] to the surface of fan triangles from ``wall_fan_triangles``."""
    W, M = b.shape[0], b.shape[1]
    d = torch.empty(x.shape[0], dtype=x.dtype, device=x.device)
    todo = torch.arange(x.shape[0], device=x.device)
    for k in (16, 256, W):
        k = min(k, W)
        chunk = max(1, min(budget // (k * M * 3), budget // W))
        left = []
        for s in range(0, todo.numel(), chunk):
            ii = todo[s:s + chunk]
            xi = x[ii]
            bound = (xi.unsqueeze(1) - face_pos.unsqueeze(0)).norm(dim=-1) - radius.unsqueeze(0)
            bk, idx = bound.topk(k, dim=1, largest=False)
            d2 = _triangle_dist2(xi[:, None, None, :], face_pos[idx][:, :, None, :], b[idx], c[idx])
            di = d2.reshape(ii.numel(), -1).min(1)[0].sqrt()
            d[ii] = di
            if k < W:
                left.append(ii[bk[:, -1] < (1.0 - rtol) * di])
        if k >= W or not left:
            break
        todo = torch.cat(left)
        if todo.numel() == 0:
            break
    return d
