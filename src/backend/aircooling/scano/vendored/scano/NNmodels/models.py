"""Compact full-BC operators and explicitly labeled posthoc aggregation controls."""
import math
import torch
from torch import nn
from .vendor.frozen_sca.TransFVGN.TextCondTrans_v2 import TextCondTrans_v2


def features(pos, boundary_value=None):
    """Common geometry + zero/interior or scalar boundary departure + Fourier coordinates."""
    normals=torch.zeros_like(pos)
    values=torch.zeros((*pos.shape[:-1],5),dtype=pos.dtype,device=pos.device)
    if boundary_value is not None:
        normals[...,2]=1.
        values[...,4:5]=boundary_value
    freq=pos.new_tensor([1.,2.,4.,8.])*math.pi
    phase=(pos.unsqueeze(-1)*freq).flatten(-2)
    return torch.cat([pos,normals,values,phase.sin(),phase.cos()],dim=-1)


def bpos3(xy):
    """Boundary positions: K16 supplies 2D xy (lift with z=1); patchbed supplies 3D."""
    return xy if xy.shape[-1] == 3 else torch.cat([xy, torch.ones_like(xy[..., :1])], dim=-1)


class SCAOperator(nn.Module):
    """Same frozen SCA family, newly trained on scalar Laplace; not old weights."""
    def __init__(self,width=64,heads=4,slices=16,layers=2,n_ref=1024):
        super().__init__();self.n_ref=n_ref
        self.network=TextCondTrans_v2(internal_input_size=35,boundary_input_size=35,
            hidden_size=width,n_layers=layers,n_head=heads,slice_num=slices,mlp_ratio=2,
            node_output_size=1,dropout=0.,assume_single_graph_batch=False,
            use_gradient_checkpointing=False)
        self._original_dense=self.network._compute_slice_tokens_dense
        self.network._compute_slice_tokens_dense=self._dense_control
        self._mode='legacy';self._physical_weights=None

    def _dense_control(self,fx,x_proj,batch_size,nodes_per_graph,temperature,slice_projector):
        weights,token=self._original_dense(fx,x_proj,batch_size,nodes_per_graph,temperature,slice_projector)
        if slice_projector is not self.network.boundary_in_project_slice or self._mode=='legacy':
            return weights,token
        norm=weights.sum(dim=2)
        if self._mode=='count':
            return weights,token*((norm.unsqueeze(-1)+1e-5)/(norm.unsqueeze(-1)+1e-5*nodes_per_graph/self.n_ref))
        if self._mode!='measure':raise ValueError(self._mode)
        area=self._physical_weights
        area=area/area.sum(dim=1,keepdim=True)
        weighted=weights*area[:,None,:,None]
        fx_mid=fx.view(batch_size,nodes_per_graph,self.network.num_heads,self.network.dim_head).permute(0,2,1,3)
        numerator=torch.einsum('bhnd,bhng->bhgd',fx_mid,weighted)
        token=numerator/(weighted.sum(dim=2).unsqueeze(-1)+1e-5/self.n_ref)
        return weights,token

    def forward(self,query,xy,boundary_value,physical_weights,mode='legacy'):
        if mode not in ('legacy','count','measure'):raise ValueError(mode)
        b,q,_=query.shape;n=xy.shape[1]
        self._mode=mode;self._physical_weights=physical_weights
        bpos=bpos3(xy)
        qx=features(query);bx=features(bpos,boundary_value)
        qb=torch.arange(b,device=query.device).repeat_interleave(q)
        bb=torch.arange(b,device=query.device).repeat_interleave(n)
        prediction=self.network(sampled_pos=query.reshape(-1,3),sampled_unv=qx[...,3:6].reshape(-1,3),
            sampled_x_override=qx.reshape(-1,35),batch=qb,boundary_pos=bpos.reshape(-1,3),
            boundary_unv=bx[...,3:6].reshape(-1,3),boundary_batch=bb,
            patch_numerical_values=bx[...,6:].reshape(-1,29))
        return prediction.reshape(b,q,1)


class PooledFiLM(nn.Module):
    """Full-BC coordinate-aware learned set pooling; independent pointwise decoder."""
    def __init__(self,width=128):
        super().__init__()
        self.boundary=nn.Sequential(nn.Linear(35,width),nn.SiLU(),nn.Linear(width,width),nn.SiLU(),nn.Linear(width,width))
        self.query=nn.Sequential(nn.Linear(35,width),nn.SiLU(),nn.Linear(width,width),nn.SiLU())
        self.condition=nn.Linear(width,width*2)
        self.decode=nn.Sequential(nn.Linear(width,width),nn.SiLU(),nn.Linear(width,1))

    def forward(self,query,xy,boundary_value,physical_weights,mode='measure'):
        bpos=bpos3(xy)
        context=self.boundary(features(bpos,boundary_value))
        weights=physical_weights/physical_weights.sum(1,keepdim=True)
        context=(context*weights[...,None]).sum(1)
        gamma,beta=self.condition(context).chunk(2,dim=-1)
        return self.decode(self.query(features(query))*(1+gamma[:,None,:])+beta[:,None,:])


class SCAPatchOperator(nn.Module):
    """Patch-token bridge: per-patch area-weighted mean of record embeddings, then
    per-query masked cross-attention over patch tokens; FiLM decoder. Neumann records
    share one reserved token id so fixed-zero side fluxes stay representable."""
    def __init__(self, width=128, max_patches=64):
        super().__init__()
        self.maxg = max_patches + 1
        self.boundary = nn.Sequential(nn.Linear(35, width), nn.SiLU(), nn.Linear(width, width))
        self.query = nn.Sequential(nn.Linear(35, width), nn.SiLU(), nn.Linear(width, width))
        self.qk = nn.Linear(width, width, bias=False)
        self.tk = nn.Linear(width, width, bias=False)
        self.cond = nn.Linear(width, width * 2)
        self.decode = nn.Sequential(nn.Linear(width, width), nn.SiLU(), nn.Linear(width, 1))

    def forward(self, query, xy, boundary_value, physical_weights, mode='measure', patch_ids=None):
        if patch_ids is None:
            raise ValueError('sca_patch requires patch_ids')
        b, n = xy.shape[:2]
        wdim = self.boundary[0].out_features
        emb = self.boundary(features(bpos3(xy), boundary_value))
        w = physical_weights / physical_weights.sum(1, keepdim=True)
        g = self.maxg
        if bool((patch_ids < 0).any() or (patch_ids >= g).any()):
            raise ValueError('patch_ids outside token budget range')
        ids = patch_ids
        mass = torch.zeros(b, g, wdim, device=emb.device, dtype=emb.dtype)
        mass.scatter_add_(1, ids.unsqueeze(-1).expand(b, n, wdim), emb * w.unsqueeze(-1))
        denom = torch.zeros(b, g, device=emb.device, dtype=emb.dtype)
        denom.scatter_add_(1, ids, w)
        present = denom > 0
        tokens = mass / (denom.unsqueeze(-1) + 1e-12)
        qe = self.query(features(query))
        logits = (self.qk(qe) @ self.tk(tokens).transpose(1, 2)) / (wdim ** 0.5)
        logits = logits.masked_fill(~present[:, None, :], -torch.inf)
        att = torch.softmax(logits, dim=-1)
        ctx = att @ tokens
        gamma, beta = self.cond(ctx).chunk(2, dim=-1)
        return self.decode(qe * (1 + gamma) + beta)


class SCAPatchDeepOperator(nn.Module):
    """Patch-token bridge with multi-layer per-query cross-attention refinement and
    per-patch second-moment (within-patch profile spread) token features. Keeps the token
    budget at max_patches+1 while giving the query side iterative local conditioning."""
    def __init__(self, width=128, max_patches=64, depth=2):
        super().__init__()
        self.maxg = max_patches + 1
        self.boundary = nn.Sequential(nn.Linear(35, width), nn.SiLU(), nn.Linear(width, width))
        self.query = nn.Sequential(nn.Linear(35, width), nn.SiLU(), nn.Linear(width, width))
        self.tok_in = nn.Linear(2 * width, width)
        self.qk = nn.ModuleList([nn.Linear(width, width, bias=False) for _ in range(depth)])
        self.tk = nn.ModuleList([nn.Linear(width, width, bias=False) for _ in range(depth)])
        self.proj = nn.ModuleList([nn.Linear(width, width) for _ in range(depth)])
        self.ff = nn.ModuleList([nn.Sequential(nn.Linear(width, width), nn.SiLU(),
                                               nn.Linear(width, width)) for _ in range(depth)])
        self.cond = nn.Linear(width, width * 2)
        self.decode = nn.Sequential(nn.Linear(width, width), nn.SiLU(), nn.Linear(width, 1))

    def forward(self, query, xy, boundary_value, physical_weights, mode='measure', patch_ids=None):
        if patch_ids is None:
            raise ValueError('sca_patch_deep requires patch_ids')
        b, n = xy.shape[:2]
        wdim = self.boundary[0].out_features
        emb = self.boundary(features(bpos3(xy), boundary_value))
        w = physical_weights / physical_weights.sum(1, keepdim=True)
        g = self.maxg
        if bool((patch_ids < 0).any() or (patch_ids >= g).any()):
            raise ValueError('patch_ids outside token budget range')
        ids = patch_ids
        ex = ids.unsqueeze(-1).expand(b, n, wdim)
        mass = torch.zeros(b, g, wdim, device=emb.device, dtype=emb.dtype)
        mass.scatter_add_(1, ex, emb * w.unsqueeze(-1))
        mass2 = torch.zeros(b, g, wdim, device=emb.device, dtype=emb.dtype)
        mass2.scatter_add_(1, ex, emb.pow(2) * w.unsqueeze(-1))
        denom = torch.zeros(b, g, device=emb.device, dtype=emb.dtype)
        denom.scatter_add_(1, ids, w)
        present = denom > 0
        mean = mass / (denom.unsqueeze(-1) + 1e-12)
        var = mass2 / (denom.unsqueeze(-1) + 1e-12) - mean.pow(2)
        tokens = self.tok_in(torch.cat([mean, var.clamp_min(1e-8).sqrt()], -1))
        qe = self.query(features(query))
        for qk, tk, proj, ff in zip(self.qk, self.tk, self.proj, self.ff):
            logits = (qk(qe) @ tk(tokens).transpose(1, 2)) / (wdim ** 0.5)
            logits = logits.masked_fill(~present[:, None, :], -torch.inf)
            att = torch.softmax(logits, dim=-1)
            ctx = att @ tokens
            qe = qe + proj(ctx)
            qe = qe + ff(qe)
        gamma, beta = self.cond(ctx).chunk(2, dim=-1)
        return self.decode(qe * (1 + gamma) + beta)


def build_model(name,slices=16,width=None,lfe_latent=128,lfe_depth=6,max_patches=64):
    if name=='sca':return SCAOperator(slices=slices,width=64 if width is None else width)
    if name=='film':return PooledFiLM(width=128 if width is None else width)
    if name=='linear':
        from .linear_deeponet import LinearDeepONet
        return LinearDeepONet(width=128 if width is None else width)
    if name in ('lfe','lfe_mask'):
        from .lfe_adapter import LFECubeOperator
        return LFECubeOperator(width=64 if width is None else width,slices=slices,
            p_masking=.3 if name=='lfe_mask' else 0.,latent=lfe_latent,depth=lfe_depth)
    if name=='sca_patch':return SCAPatchOperator(width=128 if width is None else width,max_patches=max_patches)
    if name=='sca_patch_deep':return SCAPatchDeepOperator(width=128 if width is None else width,max_patches=max_patches)
    raise ValueError(name)
