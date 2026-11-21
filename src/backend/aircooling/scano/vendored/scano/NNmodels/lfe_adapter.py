"""Torch equations port of sprmsv/olbc CrossAttentionExtender + cube adapter.

Upstream82094ffe2d98f8dc386cdfef7ff219bb55afe267, MIT (c)2026 ETH Zurich.
License retained at controlled/vendor/lfe_official/upstream/LICENSE.
This adapts the extender to a compact Transolver; not full LX(R)/LX(G).
The frozen SCA vendor code (torch_scatter, einops) is imported only by the legacy LFECubeOperator (models 'lfe' /
'lfe_mask' of scano.NNmodels.models: K16 c01-c07, patchbed c14 / c17), so LFEExtender, and with it the config
framework's LFE route, needs neither package.
"""
import math
import torch
from torch import nn
from torch.nn import functional as F
from .attention import sdpa


def dense(a,b,bias=True):
    layer=nn.Linear(a,b,bias=bias)
    # Flax Dense's default LeCun normalized truncated normal, independent RNG.
    std=(1/math.sqrt(a))/.87962566103423978
    nn.init.trunc_normal_(layer.weight,std=std,a=-2*std,b=2*std)
    if bias:nn.init.zeros_(layer.bias)
    return layer


class FF(nn.Module):
    def __init__(self,n_in,n_out,mult=1):
        super().__init__();self.fc1=dense(n_in,n_out*mult);self.fc2=dense(n_out*mult,n_out)
    def forward(self,x):return self.fc2(F.silu(self.fc1(x)))


class CrossAttention(nn.Module):
    def __init__(self,latent,heads):
        super().__init__();self.heads=heads;self.head_dim=latent
        self.q=dense(latent,latent*heads,False);self.kv=dense(latent,latent*heads*2,False)
        self.out=dense(latent*heads,latent)
    def forward(self,x,context,mask,weights=None):
        b,n,_=x.shape;m=context.shape[1];h=self.heads;d=self.head_dim
        q=self.q(x).view(b,n,h,d).transpose(1,2)
        k,v=self.kv(context).chunk(2,-1)
        k=k.view(b,m,h,d).transpose(1,2);v=v.view(b,m,h,d).transpose(1,2)
        bias=torch.zeros((b,1,1,m),device=x.device,dtype=x.dtype)
        bias=bias.masked_fill(~mask[:,None,None,:],-torch.inf)
        if weights is not None:bias=bias+weights[:,None,None,:].log()
        # Memory-efficient equivalent scaled softmax attention; oracle parity required.
        y=sdpa(q,k,v,attn_mask=bias,scale=d**-.5)   # dropout_p 0 as before; split over queries only if huge
        return self.out(y.transpose(1,2).reshape(b,n,h*d))


class LFEExtender(nn.Module):
    def __init__(self,b_features=37,q_features=35,latent=128,heads=4,depth=6,out=16,p_masking=0.):
        super().__init__();self.p_masking=p_masking
        self.b_ff=FF(b_features,latent);self.b_ln=nn.LayerNorm(latent,eps=1e-6)
        self.q_ff=FF(q_features,latent);self.q_ln=nn.LayerNorm(latent,eps=1e-6)
        self.attn=nn.ModuleList([CrossAttention(latent,heads) for _ in range(depth)])
        self.a_ln=nn.ModuleList([nn.LayerNorm(latent,eps=1e-6) for _ in range(depth)])
        self.ff=nn.ModuleList([FF(latent,latent) for _ in range(depth)])
        self.f_ln=nn.ModuleList([nn.LayerNorm(latent,eps=1e-6) for _ in range(depth)])
        self.final=FF(latent,out);self.last_retained_indices=[]
    def forward(self,boundary,query,mask,weights=None):
        if mask.dtype!=torch.bool or not torch.all(mask.any(1)):raise ValueError('Every case requires valid BC records')
        if weights is not None and (not torch.isfinite(weights).all() or not (weights>0).all()):raise ValueError('Positive finite quadrature weights required')
        bc=self.b_ln(self.b_ff(boundary));x=self.q_ln(self.q_ff(query));self.last_retained_indices=[]
        for attn,aln,ff,fln in zip(self.attn,self.a_ln,self.ff,self.f_ln):
            c=bc;m=mask;w=weights
            if self.training:
                keep=int(bc.shape[1]*(1-self.p_masking))
                if keep<1:raise ValueError('Masking removed all BC records')
                idx=torch.randperm(bc.shape[1],device=bc.device)[:keep]
                self.last_retained_indices.append(idx.detach().cpu())
                c=bc[:,idx];m=mask[:,idx];w=None if weights is None else weights[:,idx]
                if not torch.all(m.any(1)):raise ValueError('No valid BC after subset masking')
            x=aln(x+attn(x,c,m,w));x=fln(x+ff(x))
        return self.final(x)


class LFECubeOperator(nn.Module):
    def __init__(self,width=64,slices=16,p_masking=0.,latent=128,depth=6,heads=4,out=16):
        from .vendor.frozen_sca.GraphTransolver.GraphTransolver import MLP,Transolver_block   # needs torch_scatter, einops
        super().__init__()
        self.extender=LFEExtender(latent=latent,heads=heads,depth=depth,out=out,p_masking=p_masking)
        self.lift=MLP(35,width*2,width,n_layers=0,res=False,act='gelu')
        self.placeholder=nn.Parameter(torch.rand(width)/width)
        self.fuse=nn.Linear(width+out,width)
        self.blocks=nn.ModuleList([Transolver_block(num_heads=4,hidden_dim=width,dropout=0.,act='gelu',mlp_ratio=2,slice_num=slices,assume_single_graph_batch=False) for _ in range(2)])
        self.decoder=nn.Sequential(nn.LayerNorm(width),nn.Linear(width,1))
    def forward(self,query,xy,boundary_value,physical_weights,mode='legacy'):
        from .models import bpos3, features
        b,q,_=query.shape;bpos=bpos3(xy)
        bf=features(bpos,boundary_value)
        typed=torch.cat([bf,torch.ones_like(boundary_value),torch.zeros_like(boundary_value)],-1)
        qf=features(query);mask=torch.ones(xy.shape[:2],device=xy.device,dtype=torch.bool)
        if mode not in ['legacy','count','measure']:raise ValueError(mode)
        extension=self.extender(typed,qf,mask,weights=physical_weights if mode=='measure' else None)
        h=self.fuse(torch.cat([self.lift(qf)+self.placeholder,extension],-1)).reshape(b*q,-1)
        batch=torch.arange(b,device=query.device).repeat_interleave(q)
        for block in self.blocks:h=block(h,batch)
        return self.decoder(h).reshape(b,q,1)
