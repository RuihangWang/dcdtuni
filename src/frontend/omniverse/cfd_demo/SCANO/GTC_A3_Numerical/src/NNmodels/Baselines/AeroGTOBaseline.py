"""AeroGTO (AAAI 2025) graph-transformer trunk as a DNE single-stream baseline.

Faithful port of pengwei07/AeroGTO (code/src/AeroGTO.py). The MLP / GNN / AttentionBlock /
MixerBlock / Decoder layers are copied verbatim; only three adaptations are made so the trunk
fits our sampled-cpd_cell pipeline exactly like the Transolver / MeshGraphNets baselines:

  1. Encoder widened to ingest the full 11D DNE node feature [pos(3), unv(3), bc(5)] instead of
     positions only (the original encoder consumes pos alone -> the 8 extra channels would be
     silently dropped and the baseline would degenerate to geometry-only).
  2. Edge features rebuilt in 3D from bbox-normalized positions (the released Mixer uses a 2D
     `.repeat(1,1,2)` distance quirk); we gather all 3 coords -> [rel(3), |rel|(1)] = 4D.
  3. The final autoregressive `f_t * SiLU(alpha)` scaling (alpha init 0 -> output identically 0,
     designed for delta/time-stepping) is dropped for direct normalized-field regression.

Positions are min-max normalized per subgraph before the Fourier encoder (its 2^i frequencies
assume coords in ~[0,1]). Wired as an EDGE-CONSUMING baseline: the importer supplies edge_index;
edge features are recomputed here from the normalized coords (edge_attr arg is ignored).
"""

import torch
import torch.nn as nn
from torch.utils.checkpoint import checkpoint
from torch_scatter import scatter_mean


class MLP(nn.Module):  # verbatim from AeroGTO.py
    def __init__(self, input_size=256, output_size=256, layer_norm=True,
                 n_hidden=2, hidden_size=256, act='PReLU'):
        super().__init__()
        if act == 'GELU':
            self.act = nn.GELU()
        elif act == 'SiLU':
            self.act = nn.SiLU()
        elif act == 'PReLU':
            self.act = nn.PReLU()
        if hidden_size == 0:
            f = [nn.Linear(input_size, output_size)]
        else:
            f = [nn.Linear(input_size, hidden_size), self.act]
            for _ in range(1, n_hidden):
                f.append(nn.Linear(hidden_size, hidden_size))
                f.append(self.act)
            f.append(nn.Linear(hidden_size, output_size))
            if layer_norm:
                f.append(nn.LayerNorm(output_size))
        self.f = nn.Sequential(*f)

    def forward(self, x):
        return self.f(x)


class GNN(nn.Module):  # verbatim: local message passing via scatter_mean
    def __init__(self, n_hidden=1, node_size=128, edge_size=128, output_size=None, layer_norm=False):
        super().__init__()
        output_size = output_size or node_size
        self.f_edge = MLP(input_size=edge_size + node_size * 2, n_hidden=n_hidden,
                          layer_norm=layer_norm, act='GELU', output_size=edge_size)
        self.f_node = MLP(input_size=edge_size + node_size, n_hidden=n_hidden,
                          layer_norm=layer_norm, act='GELU', output_size=output_size)

    def forward(self, V, E, edges):
        edges = edges.long()
        senders = torch.gather(V, -2, edges[..., 0].unsqueeze(-1).repeat(1, 1, V.shape[-1]))
        receivers = torch.gather(V, -2, edges[..., 1].unsqueeze(-1).repeat(1, 1, V.shape[-1]))
        edge_inpt = torch.cat([senders, receivers, E], dim=-1)
        edge_embeddings = self.f_edge(edge_inpt)
        col = edges[..., 1].unsqueeze(-1).repeat(1, 1, edge_embeddings.shape[-1])
        edge_sum = scatter_mean(edge_embeddings, col, dim=-2, dim_size=V.shape[1])
        node_inpt = torch.cat([V, edge_sum], dim=-1)
        node_embeddings = self.f_node(node_inpt)
        return node_embeddings, edge_embeddings


class AttentionBlock(nn.Module):  # verbatim: projection/linear attention with learned Q tokens
    def __init__(self, n_token=64, w_size=128, n_heads=4):
        super().__init__()
        self.channel_dim = w_size
        self.n_token = n_token
        self.softmax = nn.Softmax(dim=-1)
        self.scale = self.channel_dim ** -0.5
        self.n_heads = n_heads
        self.Q = nn.Parameter(torch.randn(self.n_token, self.channel_dim), requires_grad=True)
        self.to_q_1 = nn.Linear(self.channel_dim, self.channel_dim)
        self.to_k_1 = nn.Linear(self.channel_dim, self.channel_dim)
        self.to_v_1 = nn.Linear(self.channel_dim, self.channel_dim)
        self.attention2 = nn.MultiheadAttention(embed_dim=w_size, num_heads=n_heads, batch_first=True)
        self.to_q_2 = nn.Linear(self.channel_dim, self.channel_dim)
        self.to_k_2 = nn.Linear(self.channel_dim, self.channel_dim)
        self.to_v_2 = nn.Linear(self.channel_dim, self.channel_dim)

    def forward(self, W_0):
        B = W_0.shape[0]
        learned_Q = self.Q.unsqueeze(0).expand(B, -1, -1)
        Q_1 = self.to_q_1(learned_Q)
        K_1 = self.to_k_1(W_0)
        V_1 = self.to_v_1(W_0)
        attn1 = self.softmax(torch.einsum('bmc, bnc -> bmn', Q_1, K_1) * self.scale)
        W_1 = torch.matmul(attn1, V_1)
        W_2, _ = self.attention2(W_1, W_1, W_1)
        Q_2 = self.to_q_2(W_0)
        K_2 = self.to_k_2(W_2)
        V_2 = self.to_v_2(W_2)
        attn2 = self.softmax(torch.einsum('bnc, bmc -> bnm', Q_2, K_2) * self.scale)
        W_3 = torch.matmul(attn2, V_2)
        return W_3


class MixerBlock(nn.Module):  # verbatim: GNN + projection-attn + gated residuals
    def __init__(self, state_embedding_dim, att_embedding_dim, n_head, n_token,
                 enc_s_dim=0, idx=0, n_blocks=4):
        super().__init__()
        self.enc_s_dim = enc_s_dim
        node_size = state_embedding_dim if enc_s_dim == 0 else state_embedding_dim + enc_s_dim
        self.gnn = GNN(node_size=node_size, edge_size=state_embedding_dim,
                       output_size=state_embedding_dim, layer_norm=True)
        self.ln1 = nn.LayerNorm(att_embedding_dim)
        self.ln2 = nn.LayerNorm(att_embedding_dim)
        self.linear = nn.Linear(att_embedding_dim, att_embedding_dim)
        self.MHA = AttentionBlock(n_token=n_token, w_size=att_embedding_dim, n_heads=n_head)
        self.idx = idx
        self.n_blocks = n_blocks
        self.alpha_1 = nn.Parameter(torch.tensor(0.0), requires_grad=True)
        if self.idx < self.n_blocks:
            self.alpha_2 = nn.Parameter(torch.tensor(0.0), requires_grad=True)
        self.alpha_3 = nn.Parameter(torch.tensor(0.0), requires_grad=True)
        self.alpha_4 = nn.Parameter(torch.tensor(0.0), requires_grad=True)
        self.act = nn.SiLU()

    def forward(self, V, E, edges, s_enc):
        V_in = torch.cat([V, s_enc], dim=-1) if self.enc_s_dim > 0 else V
        v, e = self.gnn(V_in, E, edges)
        V = V + self.act(self.alpha_1) * v
        if self.idx < self.n_blocks:
            E = E + self.act(self.alpha_2) * e
        else:
            E = E + e
        W_1 = self.MHA(self.ln1(V))
        W_2 = V + self.act(self.alpha_3) * W_1
        W_3 = W_2 + self.act(self.alpha_4) * self.linear(self.ln2(W_2))
        return W_3, E


class Encoder11D(nn.Module):
    """AeroGTO Encoder widened to ingest 11D DNE features [pos(3), unv(3), bc(5)]."""

    def __init__(self, node_input_size=11, state_embedding_dim=128):
        super().__init__()
        self.enc_s_dim = 128
        # Fourier(pos): 3 coords x 9 freqs x 2(cos/sin) = 54, + raw pos(3) = 57; + (node_input_size-3) extra
        self.enc_s = MLP(input_size=54 + node_input_size, output_size=self.enc_s_dim,
                         act='SiLU', layer_norm=False)
        self.fv = MLP(input_size=self.enc_s_dim, output_size=state_embedding_dim, layer_norm=False)

    def FourierEmbedding(self, pos, pos_start, pos_length):  # verbatim
        original_shape = pos.shape
        new_pos = pos.reshape(-1, original_shape[-1])
        index = torch.arange(pos_start, pos_start + pos_length, device=pos.device).float()
        freq = 2 ** index * torch.pi
        cos_feat = torch.cos(freq.view(1, 1, -1) * new_pos.unsqueeze(-1))
        sin_feat = torch.sin(freq.view(1, 1, -1) * new_pos.unsqueeze(-1))
        embedding = torch.cat([cos_feat, sin_feat], dim=-1).view(*original_shape[:-1], -1)
        return torch.cat([embedding, pos], dim=-1)

    def forward(self, feats):
        pos = feats[..., :3]
        pos_enc = self.FourierEmbedding(pos, -4, 9)              # [B,N,57]
        enc_in = torch.cat([pos_enc, feats[..., 3:]], dim=-1)   # [B,N,54+node_input_size]
        s_enc = self.enc_s(enc_in)                              # [B,N,128]
        V = self.fv(s_enc)                                      # [B,N,state_embedding_dim]
        return V, s_enc


class Mixer(nn.Module):
    """AeroGTO Mixer: 3D edge features rebuilt from normalized coords; per-block grad checkpoint."""

    def __init__(self, N, state_embedding_dim, att_embedding_dim, n_head, n_token):
        super().__init__()
        self.fe = MLP(input_size=4, output_size=state_embedding_dim, n_hidden=1,
                      act='SiLU', layer_norm=False)  # [rel(3), |rel|(1)]
        self.blocks = nn.ModuleList([
            MixerBlock(state_embedding_dim=state_embedding_dim, att_embedding_dim=att_embedding_dim,
                       n_head=n_head, n_token=n_token, enc_s_dim=0 if i > 0 else 128,
                       idx=int(i), n_blocks=N - 1)
            for i in range(N)
        ])

    def forward(self, V, edges, node_pos, s_enc, use_ckpt=False, ckpt_every=1):
        edges = edges.long()
        idx_s = edges[..., 0].unsqueeze(-1).repeat(1, 1, 3)
        idx_r = edges[..., 1].unsqueeze(-1).repeat(1, 1, 3)
        distance = torch.gather(node_pos, -2, idx_r) - torch.gather(node_pos, -2, idx_s)
        norm = torch.sqrt((distance ** 2).sum(-1, keepdims=True))
        E = self.fe(torch.cat([distance, norm], dim=-1))       # [B,E,state_embedding_dim]
        # ckpt_every=1 -> checkpoint every block (min memory, ~2.4x slower);
        # ckpt_every=2 -> every other block (roughly half the activation memory, ~1.5x slower);
        # use_ckpt=False -> no recompute (fastest, ~19 GiB at N~107k).
        for i, block in enumerate(self.blocks):
            if use_ckpt and (i % max(1, int(ckpt_every)) == 0):
                V, E = checkpoint(block, V, E, edges, s_enc, use_reentrant=False)
            else:
                V, E = block(V, E, edges, s_enc)
        return V


class Decoder(nn.Module):  # verbatim
    def __init__(self, state_embedding_dim=128, state_size=5):
        super().__init__()
        self.final_mlp_node = nn.Sequential(
            nn.Linear(state_embedding_dim, state_embedding_dim), nn.PReLU(),
            nn.Linear(state_embedding_dim, state_embedding_dim), nn.PReLU(),
            nn.Linear(state_embedding_dim, state_size),
        )

    def forward(self, V):
        return self.final_mlp_node(V)


class AeroGTOBaseline(nn.Module):
    """AeroGTO trunk over the sampled cpd_cell graph. Edge-consuming (needs edge_index)."""

    def __init__(self, node_input_size=11, node_output_size=5, hidden_size=128,
                 att_embedding_dim=None, N_block=8, n_head=8, n_token=256,
                 use_gradient_checkpointing=False, grad_checkpoint_every=1, **kwargs):
        super().__init__()
        att = att_embedding_dim or hidden_size
        self.encoder = Encoder11D(node_input_size=node_input_size, state_embedding_dim=hidden_size)
        self.mixer = Mixer(N=N_block, state_embedding_dim=hidden_size, att_embedding_dim=att,
                           n_head=n_head, n_token=n_token)
        self.decoder = Decoder(state_embedding_dim=hidden_size, state_size=node_output_size)
        self.node_output_size = node_output_size
        self.use_gradient_checkpointing = bool(use_gradient_checkpointing)
        # Checkpoint 1-in-`grad_checkpoint_every` mixer blocks: trades activation memory for
        # recompute. 1 = every block (safest), 2 = half the blocks (balanced speed/memory).
        self.grad_checkpoint_every = int(grad_checkpoint_every)

    def forward(self, x, edge_index, edge_attr=None, batch=None, mask_output_cell=None):
        pos = x[:, :3]
        pmin, pmax = pos.amin(0, keepdim=True), pos.amax(0, keepdim=True)
        pos_n = (pos - pmin) / (pmax - pmin + 1e-8)                     # bbox-normalize -> Fourier-safe
        feats = torch.cat([pos_n, x[:, 3:]], dim=-1).unsqueeze(0)       # [1,N,node_input_size]
        edges = edge_index.t().contiguous().unsqueeze(0)               # [1,E,2] = [sender, receiver]
        node_pos = pos_n.unsqueeze(0)                                   # [1,N,3]
        V, s_enc = self.encoder(feats)
        use_ckpt = self.use_gradient_checkpointing and self.training and torch.is_grad_enabled()
        V = self.mixer(V, edges, node_pos, s_enc, use_ckpt=use_ckpt,
                       ckpt_every=self.grad_checkpoint_every)
        out = self.decoder(V).squeeze(0)                               # [N, node_output_size]
        return out.to(torch.float32)
