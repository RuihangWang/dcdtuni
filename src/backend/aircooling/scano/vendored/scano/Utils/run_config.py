"""TOML run configuration for config-driven experiments.

One variant = one file, cases/<dataset>/cNN-<name>/<Variant>/config.toml. Switching the backbone, the boundary
route (none / single-stream node fields / per-record channel / pooled global FiLM), the context of the global
state (coupled / decoupled tau*), its measure and epsilon, or the sampling parameters means editing that file,
not the code. `resolve` fills defaults and rejects unknown sections, keys and values, so every run records its
complete configuration; `fingerprint` hashes everything that defines the protocol (all but the seed), so rows
with different fingerprints are never put in one comparison table.
"""
from __future__ import annotations

import copy
import hashlib
import json
import tomllib
from pathlib import Path

DEFAULTS = {
    # name: olbc | r0s | dcts (DCTS, per-case meshes: scano.Main.dcts_common, docs/dcts_jupiter_design_2026-10-01.md;
    #       val_count must equal the 50 validation cases of its meta.json).
    'dataset': {'name': 'olbc', 'data_dir': 'data/olbc_bc4_v1', 'val_count': 256, 'val_cases': 32,
                'train_limit': 0},
    # backbone: slice = SCANO's slice trunk (c37); transolver = upstream Transolver (MIT port, zoo/transolver.py);
    #           sca = the AAAI-27 two-stream model (refine TextCondTrans_v2, zoo/sca.py; its bridge is its route);
    #           transolver_pp = Transolver++ (MIT port, zoo/transolver_pp.py; Gumbel noise only in training);
    #           mgn = MeshGraphNets as refine's baseline (zoo/mgn.py; needs the induced mesh edges);
    #           gnot = GNOT-style linear-attention operator from the paper's equations (zoo/gnot.py);
    #           geotransolver = PhysicsNeMo GeoTransolver/GALE (Apache-2.0 port, zoo/geotransolver.py; the boundary
    #           records are its geometry context, cross-attended from every block; upstream mlp_ratio is 4).
    #           abupt = AB-UPT-style anchor attention, reimplemented from the paper (zoo/abupt.py; anchors = points
    #           with ctx_w > 0, every passed point is a query; optional SCANO channel).
    #           beno = BENO (MIT port, zoo/beno.py; two message-passing branches split by source / boundary values,
    #           valid for linear PDEs only, with a boundary Transformer per block; needs the induced mesh edges).
    #           aerogto = AeroGTO graph-transformer host (AAAI 2025; our rewrite of the AAAI-27 code, zoo/aerogto.py;
    #           d58): per block a GNN step on the induced mesh edges and a projection attention of `slices` learned
    #           query tokens over all passed points; coordinates normalised by the passed points' bounding box, so
    #           coupled contexts only; mlp_ratio x width = its MLP hidden size; optional SCANO channel.
    # experts: gated FFN experts per block (gnot only; 1 = plain FFN).
    # mlp_ratio: FFN width / width; the slice trunk is fixed at 2.
    'model': {'backbone': 'slice', 'width': 128, 'depth': 4, 'heads': 4, 'slices': 32, 'mlp_ratio': 2, 'experts': 1},
    # node_bc: BC fields on the node features of boundary points (the single-stream path);
    # channel: every point attends to every boundary record (per layer, or only in the first layer);
    # global: one pooled boundary summary modulates every layer: film (zero-initialised FiLM) or adaln_zero
    #         (Unisolver/DiT adaLN-Zero: shift, scale and a gate per residual branch, blocks start as identity).
    # lfe: the LFE extender at the input (count measure as upstream, or arc weights); settings in [lfe].
    # bridge_measure: weights of the boundary records in the slice pooling of the sca bridge / the geotransolver
    #                 context (count = refine / upstream, arc = bnd_w).
    # record_sample (r0s only; A2, design section 5.3): the fraction of the boundary records the record routes read
    #                in a training step, a uniform subset drawn per (case, epoch) (1.0 = every record). The draw is
    #                uniform, so every record has pi = record_sample and design weights 1/pi would cancel in the
    #                normalisation: this is record-subsampled training, not an IPW test (design section 9.5).
    # records: what one boundary record is for the record routes. face = one record per boundary face (as every run
    #          before 2026-10-01); patch = one record per boundary patch, the area-weighted mean of its face records
    #          with the patch area as weight (scano.Datasets.patch_records; needs per-record patch ids, i.e. r0s or
    #          dcts; J2 of docs/dcts_jupiter_design_2026-10-01.md). The node features of boundary points are unchanged.
    #          slices = the face records are aggregated into `slices` area-consistent slice tokens per head, which the
    #          channel reads in place of the records (zoo/area_slices.py; PLAN section 3.3, decision d30): the channel
    #          measure weighs the records (arc = face areas), slice_read = both adds the unnormalised read (masses in
    #          units of mass_ref) to the normalised one. slice, transolver and abupt hosts.
    # channel_every (slice backbone and the plug-in hosts, channel = every_layer; A4 injection depth, PLAN section
    #          5.4; c95): layer i (from 0)
    #          reads the channel when i % channel_every == 0, so 1 = every layer (as before), 2 = layers 0, 2, ...;
    #          the other layers have no boundary read. Values >= model.depth would read only the first layer:
    #          that is channel = first_layer.
    # global_dim (global = film or adaln_zero; E4, Proposition D): 0 = the pooled summary reaches the modulation at
    #          the full width (as before); d > 0 = it first passes a linear bottleneck width -> d -> width, so every
    #          layer is modulated by a code of rank at most d (2 W d + W + d parameters more than the reference).
    # global_dropout (global = film or adaln_zero; decision d54, R29 on H8): p in [0, 1). 0 = the code always reaches
    #          the layers (as before). p > 0 = in training-mode forwards only, the code of each case (after the
    #          bottleneck) is replaced by zeros with probability p, so a dropped step trains the trunk on its other
    #          boundary path (the node fields, a channel or the LFE extender; one of them is required): the remedy for
    #          the shortcut of c73 section 1.1, where the pooled code fits coarsely in the first epochs and the per-node
    #          path never leaves the single-stream plateau. Zeros, not the batch-mean code: training is batch 1, so the
    #          batch mean is the case's own code; zero is a fixed null input carrying no case information (the
    #          zero-initialised maps then give their bias alone, a learned case-independent modulation, like the null
    #          condition of classifier-free guidance) and needs no running state. Kept codes are not rescaled;
    #          validation, selection and evaluation read the code always. The draws come from their own generator,
    #          np.random.default_rng([seed, 2]) (train_cfg; its state is in resume.pt); slice backbone only (the
    #          only one with a global route).
    # slice_assign (records = slices; c64): learned = soft slices learned from the lifted records (as before);
    #          spatial = slices grouped by spatial adjacency, a hard partition of the records by weighted recursive
    #          coordinate bisection of their positions (the first space_dim record columns; area_slices.SpatialSlicer),
    #          the fallback PLAN section 4 H5 registers.
    # slice_lse_max (slice_read = both; c64): clamp of the unnormalised read's log mass factor (area_slices.LSE_MAX =
    #          30 before c64); below it the read is unchanged, above it the factor is exp(slice_lse_max).
    # oracle_code (r0s only; global = film or adaln_zero, no other record route; R29 E4 decoding test): 0 = the
    #          global route pools the records (as before); d > 0 = the records are replaced by ONE record holding the
    #          case's exact top-d coefficients of the pooled POD of the training fields (cell volumes, centred by the
    #          training mean, standardised per mode; scano.Main.r0s_common.oracle_codes), so the route receives the
    #          optimal d-dimensional linear code of a layout-agnostic decoder and only its decoding is learned.
    # node_bc_mask (r0s only; A8 "mask", c61): in a training draw the BC fields (type one-hot, value, alpha) of the
    #          boundary-face nodes of a fraction p of the records are zeroed, as node_bc = false does for every node:
    #          the single-stream counterpart of the LFE extender's record masking (lfe.p_masking). The hidden records
    #          are the complement of a uniform subset of max(1, int(NB (1 - p))) records keyed by (case, epoch)
    #          (scano.Datasets.sampling_tricks.masked_records). Validation, selection and evaluation never mask.
    # channel_pos (abupt only; c75, R29 on olbc H4): how the per-record channel read sees positions. raw = plain
    #          dot-product attention over MLP(raw record features), every run before c75; rope = the host's RoPE on the
    #          read (queries at the points' positions, keys at the records'); host = rope, and the record tokens
    #          embedded as the host embeds its points (its sinusoidal position code and position MLP, shared, plus an
    #          MLP of the other record features). zoo/abupt.py; needs face or patch records (not slices).
# channel_rope_lmin (abupt, channel_pos rope / host; c98, R29 of task 50): the read rotates only the RoPE pairs whose
#          wavelength is at least this share of the coordinate range; 0 = every pair (every run before c98). With
#          every pair the read keys on offsets finer than a mesh cell and its error grows on a coarser mesh (c93).
    'boundary': {'node_bc': True, 'channel': 'every_layer', 'channel_measure': 'arc', 'global': 'none',
                 'global_measure': 'arc', 'lfe': 'none', 'lfe_measure': 'count', 'bridge_measure': 'count',
                 'record_sample': 1.0, 'records': 'face', 'slices': 64, 'slice_read': 'both', 'mass_ref': 1.0,
                 'types': 'kind', 'type_encoding': 'onehot', 'channel_every': 1, 'global_dim': 0,
                 'slice_assign': 'learned', 'slice_lse_max': 30.0, 'oracle_code': 0, 'node_bc_mask': 0.0,
                 'channel_pos': 'raw', 'global_dropout': 0.0},
    # LFE extender size (official paper size is latent 128, heads 4, depth 6, out 16: ~1.8M parameters alone).
    'lfe': {'latent': 64, 'heads': 2, 'depth': 4, 'out': 8, 'p_masking': 0.0},
    # mode: coupled = the global state is computed from the sampled points themselves;
    #       decoupled = from `anchors` random mesh points, independent of the sample (tau*).
    # measure: weights of the context points in the slice means (area = quadrature, count = upstream Transolver).
    # eps: rel1e-5 = 1e-5 x mean context weight (scale-free, = upstream when weights are 1, safe gradients);
    #      scale_free = no additive term (c37/c38; its backward can overflow, see slice_operator.EPS);
    #      abs1e-5 / abs1e-2 = upstream Transolver / GALE (geotransolver); rel1e-2 = scale-free GALE counterpart.
    # area_ht (r0s only; A3, design section 5.4): coupled context weighted by V / pi, pi = the inclusion probability of
    #          the training sampler (<data_dir>/inclusion/<sampler>.npz from c43; exact rho for uniform subsets).
    # anchor_design (decoupled; r0s, and dcts and olbc for uniform / stratified / stratified_volume): uniform = anchors
    #          uniform over the cells (olbc: every node), weighted by the measure (Hajek);
    #          volume_pps = anchors drawn with replacement with probability proportional to the cell volume, unit
    #          weights (Hansen-Hurwitz; needs measure = area, which the mesh cache at deployment uses);
    #          stratified = one cell drawn uniformly from each of `anchors` consecutive strata of the cells in Z-order
    #          (r0s_common.draw_anchors_stratified; on R0-S with 4096 anchors one per 2 x 2 x 2 block; on dcts per case,
    #          dcts_common._Case.z_order), weighted by the
    #          measure times 1 / pi (Hajek; c67, the anchor-noise investigation of 10-07);
    #          stratified_volume = the cells in Z-order laid end to end by volume, cut into `anchors` strata of equal
    #          volume, one cell drawn per stratum with probability proportional to its volume, unit weights
    #          (r0s_common.draw_anchors_stratified_volume; Hansen-Hurwitz like volume_pps, needs measure = area).
    #          olbc (R29 on H3, 10-07): the anchor pool is every node and the volume is the lumped node area (the
    #          measure of its mesh cache; r0s_common.anchor_pool / anchor_volume), the Z-order is the 2-D Morton order.
    # anchor_cap (abupt on dcts; 0 = no cap, as before): at most this many points with positive context weight per
    #          forward; when there are more, a uniform random subset keeps its weights and the others get 0 (AB-UPT's
    #          native anchor subsampling; attention over every point of a DCTS subgraph or mesh is not affordable).
    #          Drawn from the caller's random generator (cfg_common.cap_context).
    # anchor_pool (decoupled; r0s and dcts): cells = the decoupled anchors are drawn from the cells (as every run before
    #          the key); nodes = from every node, cells and boundary faces (R29 of 10-10, c92: a host whose boundary data
    #          enters through boundary nodes alone, AB-UPT native, otherwise never sees a boundary condition in its
    #          global state; the count-measure mesh cache at deployment already holds every node). Needs the uniform
    #          design and the count measure (the area measure gives a face weight 0). olbc: every node is a cell.
    # anchor_design = sample (decoupled, r0s; c117, R29 of the R3 review): the anchors are a second draw of the nominal
    #          sampler (the k-hop subgraph of uniformly drawn pivots, or a uniform subset, at sampling.sample_ratio),
    #          drawn from the anchor generator independently of the query draw, weighted by the measure like a coupled
    #          context (Hajek), and they pass the GN blocks on their own induced subgraph (cfg_common._forward); the
    #          mesh cache's context copy passes them on the whole mesh. context.anchors is not read. On the whole mesh
    #          (sample_ratio = 1) the state is the one of the coupled form. r0s, olbc and dcts.
    # anchor_ratio (anchor_design = sample only; 0 = sampling.sample_ratio): the ratio of the anchors' draw, smaller
    #          than the training sample's where the GPU memory needs it (dcts: a second sample of the room's size
    #          would double the memory of a step).
    'context': {'mode': 'coupled', 'measure': 'area', 'eps': 'rel1e-5', 'anchors': 1024, 'anchor_design': 'uniform',
                'anchor_cap': 0, 'anchor_pool': 'cells', 'anchor_ratio': 0.0},
    # parameters of the frozen refine sampler (scano.Datasets.refine_sampling); choose them by the measured
    # coverage, docs/sampling_2026-09-30.md section 3.
    # A1 designs (r0s only, c60; scano.Datasets.design_sampling, PLAN section 5.4):
    # regime = partition: the cells split into round(1 / sample_ratio) blocks (recursive coordinate bisection, faces
    #          with their owner cell); a draw is one block plus a khop-hop halo of cells and the faces they own, and
    #          only the block enters the loss and the matched validation.
    # faces: native = boundary faces enter as the regime draws them (every run before c60); stratified = the faces
    #          of the native draw are replaced by a stratified draw (each patch and the insulated part of each wall a
    #          stratum, max(1, round(rho m_h)) faces each, so every patch is seen; regimes scatter and uniform);
    #          adjacent (c123; r0s and dcts, k-hop regimes) = the native draw plus every boundary face whose owner cell
    #          is in it, so face coverage follows cell coverage (design_sampling.with_owned_faces).
    # halo (r0s only; c125, mock review round 7): 0 = no halo (every run before c125). H > 0 = the forward pass reads
    #          the k-hop draw grown by H more hops of the sampling graph (khop + H from the same pivots), while the loss
    #          and the matched validation read only the nodes of the k-hop draw itself (cfg_common.scored_mask): a
    #          message-passing trunk with an H-hop receptive field then sees, at every supervised node, the same
    #          neighbourhood as on the whole mesh. Regime scatter only; the anchors keep the k-hop design.
    # rho_mix (A8 "mixed rho", c61 / c62): [] = every training draw at sample_ratio (as always). Otherwise the ratio of
    #          each training draw is drawn per (case, epoch) (scano.Datasets.sampling_tricks.mixed_ratio): uniformly
    #          from the listed values (rho_mix_draw = "set"), or log-uniformly from [lo, hi] (rho_mix_draw =
    #          "log_uniform"); the sampler (regime, khop, p_scatter) is unchanged. sample_ratio stays the nominal ratio
    #          that validation, selection and evaluation deploy, and must lie in the mix's range.
    # stratify (r0s only; A8 / A1 "stratified sampling"): patch = every training draw is topped up with one face of
    #          each patch it missed (scano.Datasets.sampling_tricks.add_missed_groups; the face with the smallest key
    #          of the training salt, so on a uniform subset this is subset_sampling.stratified_face_pivots). A design
    #          change without design weights; validation, selection and evaluation keep the plain sampler.
    'sampling': {'sample_ratio': 0.05, 'khop': 2, 'regime': 'scatter', 'p_scatter': 0.5, 'faces': 'native', 'halo': 0,
                 'rho_mix': [], 'rho_mix_draw': 'set', 'stratify': 'none'},
    # loss_weight (r0s only; A3 M3 arm): uniform = mean squared error over the sampled nodes; v_over_pi = weighted by
    #          V / pi (the same pi as area_ht), i.e. the design-unbiased volume integral of the squared error;
    #          boundary faces have no volume and leave the supervision under it. inv_pi (A8 "IPW", c61) = weighted by
    #          1 / pi alone (design weights without a physical measure), normalised to mean 1 over the sampled nodes:
    #          a ratio (Hajek) estimate of the mean squared error over every node of the mesh, cells and faces (the
    #          whole-graph training loss), with c43's Monte Carlo pi; not design-unbiased (a ratio, and pi estimated).
    #          ht (A1, c60) = count-measure Horvitz-Thompson: (1/N) sum_{i in S} e_i / pi_i, design-unbiased for the
    #          whole-mesh node mean at fixed predictions (a coupled model's predictions change with the draw), pi of the
    #          k-hop or uniform design analytically and of stratified faces exactly (r0s_common.design_pi). Against
    #          the plain mean it mainly up-weights the boundary faces (~x5); it is not Proposition C's V / pi form.
    # consistency (A8, c61): lambda >= 0 of a consistency loss. 0 = none (as always). Otherwise every step takes a
    #          second, independent training draw of the same case (keyed by sampling_tricks.second_draw_epoch, a
    #          hashed key, so a k-hop pivot window is not a fixed shift of the first draw's; with its own anchors,
    #          record subset and mask) and adds lambda x the mean squared difference of the two
    #          predictions on the nodes both draws hold (in units of the training std; several fields: per field, then
    #          averaged), with gradients through both. The supervised loss is that of the first draw alone, so the
    #          supervision per step is unchanged; a step costs about two forward and backward passes.
    # matmul: fp32 = TF32 disabled for CUDA matmuls and cuDNN (every run before 2026-10-01); tf32 = TF32 allowed
    #         (J3 of docs/dcts_jupiter_design_2026-10-01.md). Applied by train_cfg, select_cfg, eval_cfg and eval_r0s.
    # loss (decision d50, 10-07): the training loss of one draw (cfg_common.train_loss). mse = the squared error in
    #         units of the training std, the mean over the nodes (per field, then averaged over the fields; every run
    #         before the key). rel_l2 = per field the relative L2 error about the training mean,
    #         sqrt(sum_n w_n (p - y)^2) / sqrt(sum_n w_n (y - mu)^2 + eps), averaged over the fields
    #         (cfg_common.rel_l2_loss): the denominator of the validation metric field_errors, not squared. w_n = the
    #         weights the mse uses (1, or train.loss_weight's). Validation, selection and evaluation do not change.
    'train': {'epochs': 20, 'lr': 1e-3, 'weight_decay': 1e-4, 'warmup_max': 1000, 'clip': 1.0, 'seed': 17,
              'loss_weight': 'uniform', 'matmul': 'fp32', 'loss': 'mse', 'consistency': 0.0},
}

CHOICES = {
    ('dataset', 'name'): {'olbc', 'r0s', 'dcts'},
    ('model', 'backbone'): {'slice', 'transolver', 'transolver_pp', 'sca', 'gnot', 'geotransolver', 'abupt', 'mgn',
                            'beno', 'aerogto'},
    ('boundary', 'channel'): {'none', 'every_layer', 'first_layer'},
    ('boundary', 'channel_measure'): {'arc', 'count'},
    ('boundary', 'global'): {'none', 'film', 'adaln_zero'},
    ('boundary', 'global_measure'): {'arc', 'count'},
    ('boundary', 'lfe'): {'none', 'input'},
    ('boundary', 'lfe_measure'): {'arc', 'count'},
    ('boundary', 'bridge_measure'): {'arc', 'count', 'off'},
    ('context', 'mode'): {'coupled', 'decoupled'},
    ('context', 'measure'): {'area', 'count', 'area_ht'},
    ('context', 'anchor_design'): {'uniform', 'volume_pps', 'stratified', 'stratified_volume', 'sample'},
    ('context', 'anchor_pool'): {'cells', 'nodes'},
    ('train', 'loss_weight'): {'uniform', 'v_over_pi', 'ht', 'inv_pi'},
    ('sampling', 'rho_mix_draw'): {'set', 'log_uniform'},
    ('sampling', 'stratify'): {'none', 'patch'},
    ('context', 'eps'): {'scale_free', 'rel1e-5', 'rel1e-2', 'abs1e-5', 'abs1e-2'},
    ('sampling', 'regime'): {'scatter', 'roi', 'uniform', 'partition'},
    ('sampling', 'faces'): {'native', 'stratified', 'adjacent'},
    ('boundary', 'records'): {'face', 'patch', 'slices'},
    ('boundary', 'slice_read'): {'both', 'normalized'},
    ('boundary', 'slice_assign'): {'learned', 'spatial'},
    # types (dcts only; J7, decision d31): kind = the kind one-hot alone (wall / patch / cyclic, as before); role = plus a
    # role one-hot (server inlet / outlet, CRAC supply / return) on the boundary node features and the records;
    # none = no boundary type at all (c50, H6): the kind one-hot of the boundary rows and records is zero (widths and
    # parameters as for kind, cells keep their cell flag). r0s (c57, A7): kind = its D / N / R type one-hot (the only
    # type information R0-S has), none = that one-hot zeroed in front of the model (zoo/type_encoding.py); no role.
    ('boundary', 'types'): {'none', 'kind', 'role'},
    # type_encoding (r0s only; c57, A7, PLAN section 3.2 and H6): how the type one-hot reaches the model.
    # onehot = as every run before (no wrapper); text = each type name (Dirichlet, Neumann, Robin) embedded once by a
    # frozen sentence encoder (data/type_text_v1) and mapped by a learned linear layer in place of the one-hot;
    # const_text = the same with one fixed sentence for every type (OperatorCLIP's constant-text control).
    ('boundary', 'type_encoding'): {'onehot', 'text', 'const_text'},
    ('boundary', 'channel_pos'): {'raw', 'rope', 'host'},
    ('train', 'matmul'): {'fp32', 'tf32'},
    ('train', 'loss'): {'mse', 'rel_l2'},
}

NOT_IN_FINGERPRINT = {('train', 'seed')}
# Keys added after the first runs (c38, 5e332b7): at these values they are left out of the fingerprint, so a
# protocol that does not use them keeps the fingerprint it had before they existed.
ADDED_LATER = {('model', 'mlp_ratio'): 2, ('boundary', 'lfe'): 'none', ('boundary', 'lfe_measure'): 'count',
               ('lfe', 'latent'): 64, ('lfe', 'heads'): 2, ('lfe', 'depth'): 4, ('lfe', 'out'): 8,
               ('lfe', 'p_masking'): 0.0, ('boundary', 'bridge_measure'): 'count', ('model', 'experts'): 1,
               # R0-S A2 / A3 keys (dev/r0s-eval, design sections 5.3-5.4)
               ('boundary', 'record_sample'): 1.0, ('context', 'anchor_design'): 'uniform',
               ('train', 'loss_weight'): 'uniform',
               # training loss (decision d50, 10-07): mse = what every run before the key trained with
               ('train', 'loss'): 'mse',
               # DCTS / JUPITER keys (dev/jupiter, docs/dcts_jupiter_design_2026-10-01.md section 3.1)
               ('boundary', 'records'): 'face', ('train', 'matmul'): 'fp32', ('context', 'anchor_cap'): 0,
               ('context', 'anchor_pool'): 'cells',          # c92 (R29 of 10-10)
               ('context', 'anchor_ratio'): 0.0,             # anchor_design = sample (c117 follow-up, 10-16)
               # area-consistent slice tokens (PLAN section 3.3)
               ('boundary', 'slices'): 64, ('boundary', 'slice_read'): 'both', ('boundary', 'mass_ref'): 1.0,
               ('boundary', 'types'): 'kind',
               # R0-S boundary-type encodings (c57, A7)
               ('boundary', 'type_encoding'): 'onehot',
               # injection depth A4 and the global-route bottleneck E4 (c58, c59; branch r0s-depth)
               ('boundary', 'channel_every'): 1, ('boundary', 'global_dim'): 0,
               # A1 stratified boundary faces (c60)
               ('sampling', 'faces'): 'native', ('sampling', 'halo'): 0,         # c125
               # c64: spatially grouped slices and the clamp of the unnormalised read (zoo/area_slices.py)
               ('boundary', 'slice_assign'): 'learned', ('boundary', 'slice_lse_max'): 30.0,
               # the oracle code of the R29 E4 decoding test (branch r29-baselines)
               ('boundary', 'oracle_code'): 0,
               # A8 training tricks (c61 / c62; scano.Datasets.sampling_tricks)
               ('sampling', 'rho_mix'): [], ('sampling', 'rho_mix_draw'): 'set', ('sampling', 'stratify'): 'none',
               ('boundary', 'node_bc_mask'): 0.0, ('train', 'consistency'): 0.0,
               # AB-UPT channel read positions (c75)
               ('boundary', 'channel_pos'): 'raw',
               # code dropout of the global route (decision d54; branch film-dropout)
               ('boundary', 'global_dropout'): 0.0}
# DCTS heat sources (d49; scano.Main.dcts_common, scano.Datasets.dcts_source; needs `dcts build-source` on the cache).
# source: none = the inputs of every run before 2026-10-07; heat = F1, the server-outlet heat-flux density and the
#         server load on the boundary-node features and the records; heat_near = F1 plus F2, the nearest server
#         outlet (load, temperature rise, offset, distance; within 3 m) on every node; heat_near_jet = F1 plus F2 in
#         that server's exhaust frame (load, rise, offset along the jet / lateral / vertical, signed distance,
#         distance, jet direction; c66 R29 diagnosis, dcts_source.node_columns_jet).
# source_measure (with source != none and a record route that weighs the records, record_weights_used): area = every
#         record weighs its face area; flux = the server-outlet face records weigh their mass flux (a |U| / U_MASS,
#         independent of the load): Proposition W1, the server pair is a jump condition whose measure is the pair's
#         mass-flux measure and whose datum is dT (a data column); heat = they weigh their load (a q / Q_MASS, kW as
#         m^2), the datum inside the measure (c66 comparison arm).
# outlet_slices (records = slices, types = role; R29 audit item 1): 0 = every record in the host's slices (as before);
#         G_o > 0 = the server-outlet records get G_o slices of their own, a spatial partition of their positions
#         (area_slices.OutletSlicer), the other records stay in the host's slices.
# bc_input: t1 = the boundary values are the first solver iteration (as before); spec = rebuilt from the case
#         specification (cfdrun boundary bindings; `dcts build-spec`): U = -/+ (Q / A) n on the server outlets / inlets and
#         CRAC supplies, T = 295.15 K, outlets 295.15 K + P / (rho cp Q), supplies their set point, zero velocity on
#         the rest, and no p_rgh (its column is zero, the training mean): no solver output enters (scano.Main.dcts_common).
# Every key of this block is registered in ADDED_LATER at its default, so the fingerprints of every earlier protocol are
# unchanged.
DEFAULTS['boundary'].update({'source': 'none', 'source_measure': 'area', 'outlet_slices': 0, 'bc_input': 't1'})
CHOICES.update({('boundary', 'source'): {'none', 'heat', 'heat_near', 'heat_near_jet'},
                ('boundary', 'source_measure'): {'area', 'heat', 'flux'},
                ('boundary', 'bc_input'): {'t1', 'spec'}})
ADDED_LATER.update({('boundary', 'source'): 'none', ('boundary', 'source_measure'): 'area',
                    ('boundary', 'outlet_slices'): 0, ('boundary', 'bc_input'): 't1'})
# Server-zone loss weight (dcts only; R29 diagnosis of the server-inlet temperatures, 10-08, docs/dcts_inlets_2026-10-08.md,
# tools/dcts_inlet_diag.py). The server zone of a case = the server-inlet and server-outlet boundary-face nodes and the
# cells whose centroid lies within zone_radius metres of one of those faces (dcts_common.server_zone_mask; the same
# distance rule as eval_dcts_zones' near_server_inlet / near_server_outlet zones at 0.3 m).
# zone_weight: 1 = off (every run before the key: the plain mean over the sampled nodes). w > 1 = the server-zone nodes of
#         a draw weigh w, every other node 1, normalised to mean 1 over the draw (cfg_common.loss_weights; the weights
#         enter the mse and rel_l2 as train.loss_weight's do). Validation, selection and evaluation do not change.
# zone_radius: metres (> 0); only read when zone_weight > 1.
# Both keys are registered in ADDED_LATER at their defaults: every earlier protocol keeps its fingerprint and describe.
DEFAULTS['train'].update({'zone_weight': 1.0, 'zone_radius': 0.3})
ADDED_LATER.update({('train', 'zone_weight'): 1.0, ('train', 'zone_radius'): 0.3})
DCTS_ONLY = {('train', 'zone_weight'), ('train', 'zone_radius')}
# Server jump condition (dcts only; R29 diagnosis of the server-inlet temperatures, fix arms B1 / B2 of
# docs/dcts_inlets_2026-10-08.md section 5). In the converged CFD a server's exhaust is its own inlet temperature + dT, but
# the outlet boundary value the model reads is the first iteration T_INIT + dT (bc_input = "spec": T_INIT + P/(rho cp Q)),
# whatever air the server draws; every DCTS model copies it (dcts_inlets section 3.2).
# server_jump (B1): none = the outlet T boundary value as cached (every run before the key). teacher = in training the
#         outlet T boundary value of every server (node features and records) is its true inlet temperature + dT (the
#         area-weighted training target over its inlet faces) with probability server_jump_p per draw (a draw key of
#         (case, epoch), cfg_common.jump_draw), else as cached. unrolled (B1u) = no true value: a no-grad pass on the same
#         draw with the cached value gives each server's inlet temperature (owner cells of its inlet faces in the draw),
#         and the training forward reads that + dT (servers without such a cell keep the cached value). Deployment
#         (every predict(); cfg_common.jump_fixed_point), both modes: pass 1 with the cached value, then up to
#         server_jump_passes - 1 more passes with the previous pass's inlet temperatures + dT, stopping once the outlet
#         input changes by less than server_jump_tol kelvin; no target value enters a deployment.
# server_jump_p: teacher probability in (0, 1]; server_jump_passes: deployment passes in 2..5; server_jump_tol: kelvin > 0.
# server_route (B2): none, or inlet_pool = at every layer the hidden state of each server's inlet owner cells among the
#         query rows (mean) goes through a zero-initialised LayerNorm + Linear of that layer and is added to the lifted
#         records of that server's outlet faces; the slice tokens are updated with the slice assignment of the unrouted
#         records (zoo/slice_operator.py, SliceOperator.route_tokens). Slice backbone, learned slice tokens only.
DEFAULTS['boundary'].update({'server_jump': 'none', 'server_jump_p': 0.5, 'server_jump_passes': 3,
                             'server_jump_tol': 0.05, 'server_route': 'none'})
CHOICES.update({('boundary', 'server_jump'): {'none', 'teacher', 'unrolled'},
                ('boundary', 'server_route'): {'none', 'inlet_pool'}})
ADDED_LATER.update({('boundary', 'server_jump'): 'none', ('boundary', 'server_jump_p'): 0.5,
                    ('boundary', 'server_jump_passes'): 3, ('boundary', 'server_jump_tol'): 0.05,
                    ('boundary', 'server_route'): 'none'})
DCTS_ONLY |= {('boundary', 'server_jump'), ('boundary', 'server_jump_p'), ('boundary', 'server_jump_passes'),
             ('boundary', 'server_jump_tol'), ('boundary', 'server_route')}
# keys implemented for the r0s dataset only (elsewhere they have no effect, so setting them is an error); dcts and olbc
# also take context.anchor_design (uniform, stratified or stratified_volume: DCTS_ANCHOR_DESIGNS)
R0S_ONLY = {('boundary', 'record_sample'), ('context', 'anchor_design'), ('train', 'loss_weight'),
            ('boundary', 'type_encoding'), ('sampling', 'faces'), ('sampling', 'stratify'), ('boundary', 'node_bc_mask'),
            ('sampling', 'halo')}
DCTS_ANCHOR_DESIGNS = {'uniform', 'stratified', 'stratified_volume', 'sample'}
# list-valued keys (their elements are numbers, stored as floats)
LISTS = {('sampling', 'rho_mix')}
# message_passing (model; slice backbone; c76 on dcts, d54 on r0s and olbc): m >= 1 FVGN graph-net blocks before each
#         trunk layer, as the original SCANO trunk had them (AAAI-27 TransFVGN AttnProcessor: the layer reads x + GN(x);
#         zoo/fvgn_gn.py), on the mesh graph among the queried points (scano.Main.gn_common: dcts the mesh faces, r0s the
#         message graph, olbc cpd|neighbor_cell, one edge per pair; the k-hop training subgraph, the whole mesh at
#         deployment). Under a decoupled context the GN acts on the queries only and the anchors / the mesh cache's
#         context copy carry the trunk without it, so the global state is the same estimator in training and
#         deployment. 0 = off (every run before the key).
# gn_context (model; message_passing >= 1, r0s and olbc; d54): subgraph = the GN reads the mesh edges among the queried
#         points (the training draw; c76). mesh = the GN always runs on the whole mesh: every node is a query row, the
#         context is the drawn nodes (coupled) or the anchors (decoupled), the loss and the scores read the drawn nodes
#         (cfg_common.predict_on). Needed for uniform-subset draws, where an edge survives with probability rho^2.
DEFAULTS['model'].update({'message_passing': 0, 'gn_context': 'subgraph'})
CHOICES[('model', 'gn_context')] = {'subgraph', 'mesh'}
ADDED_LATER.update({('model', 'message_passing'): 0, ('model', 'gn_context'): 'subgraph'})
# channel_init (boundary; Transolver host with a SCANO channel; c84, 10-10): host = the host's trunc_normal(0.02)
#         re-initialisation is also applied to the channel modules and the record lift (every run before the key);
#         default = they keep PyTorch's default initialisation, as the SCANO trunk's channel does (c79 diagnosis: under
#         the host init the channel's scores start near zero and on R0-S it stays an area-mean read). The host's own
#         parameters are created first and are the same at the same seed either way.
DEFAULTS['boundary'].update({'channel_init': 'host'})
CHOICES[('boundary', 'channel_init')] = {'host', 'default'}
ADDED_LATER.update({('boundary', 'channel_init'): 'host'})
DEFAULTS['boundary'].update({'channel_rope_lmin': 0.0})        # c98: see the boundary section's comment
ADDED_LATER.update({('boundary', 'channel_rope_lmin'): 0.0})
# record_read (boundary; the slice backbone reading face records, records = "face"; R29 of 10-10, PLAN R4): normalized
#         = the per-record read with the log-area logit alone (every run before the key); both = plus the unnormalised
#         read sum_y (a_y / mass_ref) exp(q.k_y) v_y (area_slices.mass_read, output initialised at zero, created after
#         every other parameter), the G = N_b end of the slice tokens' two reads: fluxes and heat loads get their
#         summation path also where every face is its own token. Needs an explicit boundary.mass_ref of the order of a
#         case's boundary area.
DEFAULTS['boundary'].update({'record_read': 'normalized'})
CHOICES[('boundary', 'record_read')] = {'normalized', 'both'}
ADDED_LATER.update({('boundary', 'record_read'): 'normalized'})
# global_pool (boundary; global = film or adaln_zero on the slice backbone; mock review item 12): mean = one weighted mean
#         of the lifted records modulates every layer (every run before the key); attention = each layer reads the same
#         records through its own learned queries (zoo/slice_operator.py AttnPool, log-weight bias of global_measure),
#         a query-independent read of exactly the records SCANO's channel reads per point. Not with global_dim or
#         oracle_code.
DEFAULTS['boundary'].update({'global_pool': 'mean'})
CHOICES[('boundary', 'global_pool')] = {'mean', 'attention'}
ADDED_LATER.update({('boundary', 'global_pool'): 'mean'})
# Learning-rate schedule and weight averaging (decision d59, 10-08; case c82; scano.Main.train_cfg.lr_factor / Ema).
# T = epochs x training cases optimiser steps, W the warm-up steps, r = lr_min_ratio; the factor multiplies train.lr.
# schedule: cosine = the schedule of every run before the key, min(1, (t + 1) / W) * (r + (1 - r) * 0.5 (1 + cos(pi t / T)))
#           (the cosine runs over all T steps, warm-up included; with r = 0 train_cfg uses the original expression);
#           onecycle = OneCycle-style: linear warm-up to the peak over W steps, then a cosine from the peak to r over the
#           remaining T - W steps (no momentum cycling; the higher peak and the longer warm-up are train.lr and
#           train.warmup_frac); wsd = warmup-stable-decay: linear warm-up over W steps, the peak until
#           T_c = T - round(cooldown_frac T), then the "1 - sqrt" cooldown r + (1 - r) (1 - sqrt((t - T_c) / (T - T_c))).
# lr_min_ratio: the floor r of the anneal as a fraction of train.lr (0 = to zero, as before; < 1).
# warmup_frac: 0 = W = min(warmup_max, T // 10 + 1), the rule of every run before the key (the warm-up never exceeds
#           10 % of the steps); f > 0 = W = max(1, round(f T)), for warm-ups longer than 10 % (then warmup_max must stay
#           at its default: one warm-up key per variant).
# cooldown_frac: wsd only, the share of the steps in the cooldown (0.2: Hagele et al. 2024).
# ema_decay: 0 = off (as before); d in (0, 1) = an exponential moving average of the weights, updated after every
#           optimiser step with decay min(d, (1 + t) / (10 + t)) at step t (the usual warm-up of the average). The
#           average is what the run reports: the per-epoch validation (history "val", status "last_val" / "final_val")
#           and checkpoint.pt "model" are the averaged weights, so select_cfg, eval_r0s / eval_cfg and their canary
#           read the same weights; the raw weights are kept as checkpoint.pt "model_raw" and validated alongside
#           (history "val_raw", status "final_val_raw"). The average is part of resume.pt. It never feeds back into
#           training: the raw trajectory is the one the run without it computes.
# Every key is registered in ADDED_LATER at its default, so every earlier fingerprint and describe string is unchanged.
DEFAULTS['train'].update({'schedule': 'cosine', 'lr_min_ratio': 0.0, 'warmup_frac': 0.0, 'cooldown_frac': 0.2,
                          'ema_decay': 0.0})
CHOICES[('train', 'schedule')] = {'cosine', 'onecycle', 'wsd'}
ADDED_LATER.update({('train', 'schedule'): 'cosine', ('train', 'lr_min_ratio'): 0.0, ('train', 'warmup_frac'): 0.0,
                    ('train', 'cooldown_frac'): 0.2, ('train', 'ema_decay'): 0.0})
# DCTS paired-intervention training (row C of the c71 decision table; docs/dcts_paired_design_2026-10-07.md;
# scano.Main.dcts_common.open_pairs / pair_term). Off by default: every key below is registered in ADDED_LATER at its
# default, so the fingerprints of every earlier protocol are unchanged, and train_cfg computes exactly what it computed
# before (no extra forward, no random stream touched).
# pairs_dir (dcts): a cache of single-knob interventions of TRAINING rooms (cases <base>_<knob> solved on their base
#         room's mesh; tools/dcts_regen/jupiter/make_train_interventions.py), with the run cache's meta.json. A pair
#         whose base is a validation or test room is refused. '' = off.
# pair_weight (> 0 with pairs_dir): weight of the pair term added to the training loss of a step whose room has
#         pairs (one of its pairs, drawn per visit from a separate stream, seed + 2), evaluated on the SAME sampled
#         nodes and decoupled anchors as the room's own term (common random numbers: the difference of the two
#         predictions comes from the boundary inputs alone).
# pair_loss: diff = the relative L2 error of the predicted response (f_pair - f_room) against the CFD response
#         (y_pair - y_room) over the draw, per field in pair_fields (cfg_common.rel_l2_loss about zero);
#         sample = the pair's own field loss (train.loss on the pair's targets), i.e. the pair as an extra sample
#         in the same step (the ablation of the difference term).
# pair_fields (pair_loss = diff): T = the temperature response only (what R1 / R2 read); all = the five fields.
DEFAULTS['dataset'].update({'pairs_dir': ''})
DEFAULTS['train'].update({'pair_weight': 0.0, 'pair_loss': 'diff', 'pair_fields': 'T'})
CHOICES.update({('train', 'pair_loss'): {'diff', 'sample'}, ('train', 'pair_fields'): {'T', 'all'}})
ADDED_LATER.update({('dataset', 'pairs_dir'): '', ('train', 'pair_weight'): 0.0, ('train', 'pair_loss'): 'diff',
                    ('train', 'pair_fields'): 'T'})
# act_checkpoint (c120: training on the whole DCTS room): paired = activation checkpointing (fvgn_gn.gn_checkpointing)
#         on the paired steps of a pairs_dir run only, as train_cfg did before the key (c74, c86); always = on every
#         training step, and the slice tokens and every layer of the SCANO backbone as well (gn_checkpointing
#         layers=True), for runs whose step does not fit one GPU otherwise (Transolver + BQA on the largest room at
#         sample_ratio 1, c112; SCANO with G > 64 on a paired step, c120 pilot). The checkpointed pieces are recomputed in the backward with the same arithmetic
#         (tests/test_zoo_transolver.py, tests/test_dcts_pairs.py), so the key changes memory and time only; registered
#         in ADDED_LATER at its default, so every earlier fingerprint and describe string is unchanged.
DEFAULTS['train'].update({'act_checkpoint': 'paired'})
CHOICES[('train', 'act_checkpoint')] = {'paired', 'always'}
ADDED_LATER.update({('train', 'act_checkpoint'): 'paired'})
# aerogto_init (model; aerogto only; c81 addendum A1, 10-09): the AeroGTO host's initialisation. aaai = the AAAI
#         pipeline's blanket re-initialisation (every Linear trunc_normal(0.02), bias 0; c81 stage 1, every earlier run);
#         author = the AeroGTO authors' init_weights (xavier_uniform, bias 0.01 for every Linear and MultiheadAttention).
#         Host parameters only: the SCANO channel keeps PyTorch's default initialisation either way (zoo/aerogto.py).
DEFAULTS['model'].update({'aerogto_init': 'aaai'})
CHOICES[('model', 'aerogto_init')] = {'aaai', 'author'}
ADDED_LATER.update({('model', 'aerogto_init'): 'aaai'})
# aerogto_box (model; aerogto only; host matrix c95, d63): sample = coordinates normalised by the passed points'
#         bounding box (the refine computation, every run before the key; the global state then depends on the
#         sample, so coupled contexts only); domain = by the dataset's fixed coordinate range (cfg_common.DATASETS
#         pos_range), which lets the state be decoupled (context.mode = "decoupled": SCANO's component (2) on AeroGTO).
DEFAULTS['model'].update({'aerogto_box': 'sample'})
CHOICES[('model', 'aerogto_box')] = {'sample', 'domain'}
ADDED_LATER.update({('model', 'aerogto_box'): 'sample'})
# near_radius / near_k (boundary; records = slices on the slice backbone and AB-UPT; c102, the fix registered in c96
#         section 3 for the compression loss of the slice tokens): near_radius = r > 0 adds the near-field read beside the
#         token read (zoo/area_slices.NearFieldRead): every point also reads the face records within distance r of it
#         (in the dataset's coordinates) by per-record attention with the log-area logit and the record's offset / r in
#         its key, at most near_k of them (the nearest); 0 = off (every run before the keys). Both keys are registered
#         in ADDED_LATER at their defaults: every earlier protocol keeps its fingerprint and describe.
DEFAULTS['boundary'].update({'near_radius': 0.0, 'near_k': 64})
ADDED_LATER.update({('boundary', 'near_radius'): 0.0, ('boundary', 'near_k'): 64})
# backbones whose boundary path reads the records without a route switch (their bridge / context / boundary blocks)
RECORD_BACKBONES = {'sca', 'gnot', 'geotransolver', 'beno'}
# hosts that read the SCANO channel through zoo/channel_plugin.py (c95, d63)
PLUGIN_HOSTS = {'mgn', 'gnot', 'geotransolver'}

# What each backbone implements; other combinations are rejected rather than silently ignored.
SUPPORTED = {
    'slice': {('model', 'mlp_ratio'): {2}, ('boundary', 'bridge_measure'): {'count'}, ('model', 'experts'): {1}},
    'transolver': {('boundary', 'global'): {'none'}, ('boundary', 'bridge_measure'): {'count'}, ('model', 'experts'): {1}},
    'transolver_pp': {('boundary', 'global'): {'none'}, ('boundary', 'lfe'): {'none'},
                      ('boundary', 'bridge_measure'): {'count'}, ('model', 'experts'): {1}},
    'sca': {('boundary', 'channel'): {'none'}, ('boundary', 'global'): {'none'}, ('boundary', 'lfe'): {'none'},
            ('model', 'experts'): {1}},
    # gnot has no slices and its linear attention has its own denominator: slices/eps stay at their defaults.
    # mgn: local message passing (depth = message-passing steps, width = hidden); no global state, no slices,
    # no records: only the single-stream node fields, coupled context, and default values for unused keys.
    # mgn, gnot, geotransolver: boundary.channel = the SCANO channel as a plug-in after the host's blocks
    # (zoo/channel_plugin.py; host matrix c95, d63); heads = the channel's heads (mgn: 4, as before).
    'mgn': {('boundary', 'global'): {'none'}, ('boundary', 'lfe'): {'none'},
            ('boundary', 'bridge_measure'): {'count'}, ('model', 'experts'): {1}, ('model', 'slices'): {32},
            ('model', 'heads'): {4}, ('model', 'mlp_ratio'): {2}, ('context', 'mode'): {'coupled'},
            ('context', 'measure'): {'area'}, ('context', 'eps'): {'rel1e-5'}},
    'gnot': {('boundary', 'global'): {'none'}, ('boundary', 'lfe'): {'none'},
             ('model', 'slices'): {32}, ('context', 'eps'): {'rel1e-5'}},
    'geotransolver': {('boundary', 'global'): {'none'},
                      ('boundary', 'lfe'): {'none'}, ('model', 'experts'): {1}},
    # abupt: softmax attention over the anchors (no slices, no eps, no bridge); its boundary routes are the node
    # fields and the SCANO channel.
    # boundary.lfe: the LFE input route on the input tokens (c95 / H10c, as on the Transolver host)
    'abupt': {('model', 'slices'): {32}, ('model', 'experts'): {1}, ('context', 'eps'): {'rel1e-5'},
              ('boundary', 'bridge_measure'): {'count'}, ('boundary', 'global'): {'none'}},
    # beno: width = latent = MLP hidden = boundary Transformer width, depth = blocks per branch, heads = boundary
    # Transformer heads; the records reach every block through its Transformer and are max-pooled (count). No
    # global state over the interior points: coupled context only, unused keys at their defaults.
    'beno': {('boundary', 'channel'): {'none'}, ('boundary', 'global'): {'none'}, ('boundary', 'lfe'): {'none'},
             ('boundary', 'bridge_measure'): {'count'}, ('model', 'experts'): {1}, ('model', 'slices'): {32},
             ('model', 'mlp_ratio'): {2}, ('context', 'mode'): {'coupled'}, ('context', 'measure'): {'area'},
             ('context', 'eps'): {'rel1e-5'}},
    # aerogto (d58): model.slices = its learned query tokens (256 in the AAAI code), heads = the token attention's
    # heads (and the channel's), mlp_ratio x width = its MLP hidden size. Its tokens read the passed points with the
    # context weights as a log bias (area or count), but the bounding-box normalisation and the GNN see every passed
    # point, so the global state is always the sample's: coupled only. Its boundary routes are the node fields and the
    # SCANO channel.
    # c95: with model.aerogto_box = "domain" the state can be decoupled (checked in resolve)
    'aerogto': {('boundary', 'global'): {'none'}, ('boundary', 'lfe'): {'none'},
                ('boundary', 'bridge_measure'): {'count'}, ('model', 'experts'): {1},
                ('context', 'measure'): {'area', 'count'}, ('context', 'eps'): {'rel1e-5'}},
}
# graph backbones that need the mesh edges among the passed points (zoo needs_edges); under a uniform point subset an
# edge survives with probability rho^2, so they would train on an almost edgeless graph (d58: aerogto and beno are
# refused there; mgn is unchanged)
EDGE_BACKBONES_NO_UNIFORM = {'aerogto', 'beno'}


def _check_type(where, value, default):
    if isinstance(default, bool):
        ok = isinstance(value, bool)
    elif isinstance(default, int):
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif isinstance(default, float):
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    else:
        ok = isinstance(value, type(default))
    if not ok:
        raise ValueError(f'{where}: expected {type(default).__name__}, got {value!r}')


def reads_records(cfg: dict) -> bool:
    """Whether some route of the model reads the boundary records (channel, global pooling, LFE, bridge)."""
    b = cfg['boundary']
    return (b['channel'] != 'none' or b['global'] != 'none' or b['lfe'] != 'none'
            or cfg['model']['backbone'] in RECORD_BACKBONES)


def record_weights_used(cfg: dict) -> bool:
    """Whether some record route weighs the records by their weights (an area-type measure: the channel, the global
    pooling, the LFE extender or the bridge under "arc", or patch records, which average by weight); only then does
    boundary.source_measure change anything."""
    b, m = cfg['boundary'], cfg['model']
    return ((b['channel'] != 'none' and b['channel_measure'] == 'arc')
            or (b['global'] != 'none' and b['global_measure'] == 'arc')
            or (b['lfe'] != 'none' and b['lfe_measure'] == 'arc')
            or (m['backbone'] in ('sca', 'gnot', 'geotransolver') and b['bridge_measure'] == 'arc')
            or (b['records'] == 'patch' and reads_records(cfg)))


def _inactive(cfg: dict) -> set:
    """Keys that have no effect under this configuration; setting one explicitly is an error."""
    b, c, s = cfg['boundary'], cfg['context'], cfg['sampling']
    off = set()
    if cfg['dataset']['name'] in ('dcts', 'olbc'):
        off |= R0S_ONLY - {('context', 'anchor_design')}         # dcts, olbc: uniform / stratified anchors (resolve)
        if cfg['dataset']['name'] == 'dcts':
            off.discard(('sampling', 'faces'))                     # faces = "adjacent" (c123) also on dcts
    elif cfg['dataset']['name'] != 'r0s':
        off |= R0S_ONLY
    if cfg['dataset']['name'] != 'dcts':
        off |= DCTS_ONLY
    if cfg['train']['zone_weight'] == 1.0:        # no server-zone weight: no radius to read
        off.add(('train', 'zone_radius'))
    if not (cfg['model']['backbone'] == 'slice' and b['records'] == 'face' and b['channel'] != 'none'):
        off.add(('boundary', 'record_read'))      # only the slice backbone's per-record channel has the second read
    if b['server_jump'] == 'none':                # no jump inputs: no teacher probability, no deployment passes
        off |= {('boundary', 'server_jump_p'), ('boundary', 'server_jump_passes'), ('boundary', 'server_jump_tol')}
    elif b['server_jump'] != 'teacher':
        off.add(('boundary', 'server_jump_p'))
    if not reads_records(cfg):
        off |= {('boundary', 'record_sample'), ('boundary', 'records')}
        if not b['node_bc']:                      # no route reads a boundary type: nothing to encode
            off.add(('boundary', 'type_encoding'))
    if b['channel'] == 'none':
        off |= {('boundary', 'channel_measure'), ('boundary', 'channel_pos')}
    if b['channel'] != 'every_layer':
        off.add(('boundary', 'channel_every'))
    if b['source'] == 'none' or not record_weights_used(cfg):
        off.add(('boundary', 'source_measure'))
    if b['records'] != 'slices' or not reads_records(cfg):
        off.add(('boundary', 'outlet_slices'))
    if b['records'] != 'slices' or not b.get('near_radius', 0.0):
        off.add(('boundary', 'near_k'))
    if b['records'] != 'slices':
        off.add(('boundary', 'near_radius'))
    if b['records'] != 'slices':
        off |= {('boundary', 'slices'), ('boundary', 'slice_read'), ('boundary', 'slice_assign'),
                ('boundary', 'slice_lse_max')}
        if b.get('record_read', 'normalized') != 'both':
            off.add(('boundary', 'mass_ref'))
    elif b['slice_read'] == 'normalized':
        off |= {('boundary', 'mass_ref'), ('boundary', 'slice_lse_max')}
    if b['global'] == 'none' or cfg['model']['backbone'] != 'slice':
        off.add(('boundary', 'global_pool'))
    if b['global'] == 'none':
        off |= {('boundary', 'global_measure'), ('boundary', 'global_dim'), ('boundary', 'oracle_code'),
                ('boundary', 'global_dropout')}
    if b['lfe'] == 'none':
        off |= {('boundary', 'lfe_measure')} | {('lfe', k) for k in DEFAULTS['lfe']}
    if cfg['model']['backbone'] not in ('sca', 'gnot', 'geotransolver'):
        off.add(('boundary', 'bridge_measure'))
    if c['mode'] == 'coupled':
        off |= {('context', 'anchors'), ('context', 'anchor_design'), ('context', 'anchor_pool'),
                ('context', 'anchor_ratio')}
    elif c['anchor_design'] != 'sample':          # only the sample design draws anchors at a ratio (c117 follow-up)
        off.add(('context', 'anchor_ratio'))
    if cfg['dataset']['name'] == 'olbc':                # every olbc node is a cell of its 2-D mesh
        off.add(('context', 'anchor_pool'))
    if cfg['model']['backbone'] != 'abupt':
        off |= {('context', 'anchor_cap'), ('boundary', 'channel_pos')}
    if (cfg['model']['backbone'] != 'abupt' or b['channel'] == 'none'
            or b.get('channel_pos', 'raw') == 'raw'):                   # c98: only the rotated read has pairs to drop
        off.add(('boundary', 'channel_rope_lmin'))
    if s['regime'] in ('scatter', 'uniform', 'partition'):
        off.add(('sampling', 'p_scatter'))
    if s['regime'] == 'uniform':                  # a point subset without a k-hop halo
        off.add(('sampling', 'khop'))
    if s['regime'] == 'partition':                # a block holds every boundary face it owns
        off.add(('sampling', 'faces'))
    if not s['rho_mix']:
        off.add(('sampling', 'rho_mix_draw'))
    if not b['node_bc']:                          # no BC fields on the nodes to hide
        off.add(('boundary', 'node_bc_mask'))
    if not cfg['model'].get('message_passing', 0):      # no GN blocks: no GN graph to choose (d54)
        off.add(('model', 'gn_context'))
    if cfg['model']['backbone'] != 'transolver' or b['channel'] == 'none':   # c84: the Transolver host's channel only
        off.add(('boundary', 'channel_init'))
    if cfg['train'].get('schedule', 'cosine') != 'wsd':   # d59: the cooldown share belongs to the wsd schedule
        off.add(('train', 'cooldown_frac'))
    t = cfg['train']
    if not cfg['dataset']['pairs_dir']:            # paired-intervention training off
        off |= {('train', 'pair_loss'), ('train', 'pair_fields')}
    elif t['pair_loss'] == 'sample':
        off.add(('train', 'pair_fields'))
    if cfg['model']['backbone'] != 'aerogto':          # only the AeroGTO host has these switches (A1, c95)
        off |= {('model', 'aerogto_init'), ('model', 'aerogto_box')}
    return off


def resolve(raw: dict) -> dict:
    """Defaults + `raw`; unknown sections, keys or values, unsupported switches and explicitly set keys that
    have no effect raise ValueError."""
    raw = canonical(copy.deepcopy(raw))
    cfg = copy.deepcopy(DEFAULTS)
    explicit = {(sec, key) for sec, body in raw.items() if isinstance(body, dict) for key in body}
    for sec, body in raw.items():
        if sec not in cfg:
            raise ValueError(f'unknown section [{sec}]')
        if not isinstance(body, dict):
            raise ValueError(f'[{sec}] must be a table')
        for key, value in body.items():
            if key not in cfg[sec]:
                raise ValueError(f'unknown key {sec}.{key}')
            _check_type(f'{sec}.{key}', value, DEFAULTS[sec][key])
            if isinstance(DEFAULTS[sec][key], float):
                value = float(value)
            if (sec, key) in LISTS:
                if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
                    raise ValueError(f'{sec}.{key}: expected a list of numbers, got {value!r}')
                value = [float(v) for v in value]
            allowed = CHOICES.get((sec, key))
            if allowed is not None and value not in allowed:
                raise ValueError(f'{sec}.{key} = {value!r}; allowed: {sorted(allowed)}')
            cfg[sec][key] = value
    for (sec, key), allowed in SUPPORTED[cfg['model']['backbone']].items():
        if cfg[sec][key] not in allowed:
            raise ValueError(f"backbone {cfg['model']['backbone']!r} does not support {sec}.{key} = {cfg[sec][key]!r}")
    dead = sorted(explicit & _inactive(cfg))
    if dead:
        raise ValueError('keys with no effect under this configuration: '
                         + ', '.join(f'{sec}.{key}' for sec, key in dead))
    m = cfg['model']
    if m['backbone'] == 'abupt' and (m['width'] % m['heads'] or (m['width'] // m['heads']) % 4):
        # attention over all points: a head size not divisible by 4 disables the memory-efficient SDPA kernel
        # (fp32, RTX 4090 / torch 2.7) and falls back to an explicit N x N matrix
        raise ValueError('abupt needs width divisible by heads and a head size divisible by 4')
    if m['backbone'] == 'beno' and (m['width'] % 2 or m['width'] % m['heads']):
        # sinusoidal position encoding of the boundary tokens needs sin/cos pairs; heads split the width
        raise ValueError('beno needs an even width divisible by heads')
    if m['backbone'] == 'aerogto' and cfg['context']['mode'] != 'coupled' and m['aerogto_box'] != 'domain':
        raise ValueError('aerogto: a decoupled state needs model.aerogto_box = "domain" (with the sample\'s bounding '
                         'box every point\'s embedding, and so the state, depends on the sample)')
    if m['backbone'] == 'aerogto' and (m['width'] % m['heads'] or m['slices'] < 1 or m['mlp_ratio'] < 1):
        raise ValueError('aerogto needs width divisible by heads (token attention and channel), model.slices >= 1 '
                         'learned query tokens and mlp_ratio >= 1')
    if (m['backbone'] in EDGE_BACKBONES_NO_UNIFORM and cfg['sampling']['regime'] == 'uniform'
            and cfg['sampling']['sample_ratio'] < 1):
        raise ValueError(f"backbone {m['backbone']!r} passes messages on the mesh edges among the drawn points; a "
                         'uniform point subset keeps an edge with probability rho^2, so it would train on an almost '
                         'edgeless graph: use a k-hop (scatter) or roi draw')
    if not 0 <= cfg['lfe']['p_masking'] < 1:
        raise ValueError('lfe.p_masking must be in [0, 1)')
    s = cfg['sampling']
    if not 0 < s['sample_ratio'] <= 1 or s['khop'] < 0:
        raise ValueError('sampling.sample_ratio must be in (0, 1] and sampling.khop >= 0')
    if s['halo'] < 0 or (s['halo'] and (s['regime'] != 'scatter' or cfg['train'].get('loss_weight', 'uniform') != 'uniform'
                                        or cfg['context']['measure'] == 'area_ht')):
        raise ValueError('sampling.halo >= 0; a halo needs sampling.regime "scatter", train.loss_weight "uniform" and a '
                         'context measure other than "area_ht" (the halo nodes have no inclusion probabilities)')
    if s['faces'] == 'adjacent' and s['regime'] not in ('scatter', 'roi'):
        # partition draws already carry the faces of their cells; a uniform subset has no k-hop draw to close
        raise ValueError('sampling.faces = "adjacent" closes k-hop draws: it needs sampling.regime "scatter" or "roi"')
    if s['faces'] == 'adjacent' and (cfg['context']['measure'] == 'area_ht'
                                     or cfg['train'].get('loss_weight', 'uniform') != 'uniform'):
        # r0s_common.attach_design computes the inclusion probabilities of the native designs only
        raise ValueError('sampling.faces = "adjacent" has no inclusion probabilities: use context.measure "area" and '
                         'train.loss_weight "uniform"')
    d, c, b = cfg['dataset'], cfg['context'], cfg['boundary']
    if d['name'] == 'dcts':
        if m['backbone'] == 'beno':
            # decision d58 (10-08): BENO's two branches are a Green's-function split, u = A(source) + B(boundary
            # values), exact only for linear PDEs; the buoyant RANS of DCTS is not linear. Besides, every block
            # attends densely over all N_B records (hundreds of thousands of faces per DCTS case).
            raise ValueError('beno is not run on dcts (decision d58): its two-branch split u = A(source) + '
                             'B(boundary values) relies on linear superposition, which the buoyant RANS of '
                             'DCTS does not satisfy (and every block attends densely over all boundary records)')
        if d['data_dir'] == DEFAULTS['dataset']['data_dir']:
            raise ValueError('dataset.name = "dcts" needs dataset.data_dir (for example "data/dcts_cache_v1")')
        if c['mode'] == 'decoupled' and ('context', 'anchors') not in explicit:
            # the global default (1024) is olbc's; a DCTS case has about a million cells
            raise ValueError('dcts decoupled runs need an explicit context.anchors')
        if s['faces'] == 'stratified':
            raise ValueError('sampling.faces = "stratified" is implemented for dataset "r0s" only (the strata are '
                             'the R0-S patch layouts); dcts takes "native" or "adjacent"')
        if s['regime'] == 'uniform':
            # select_cfg and eval_dcts deploy dcts runs with refine's k-hop inference at (sample_ratio, khop) (TTA and
            # the single scatter / box subgraphs); a uniform point subset has no khop (the key is inactive there and
            # would silently take its default), so the deployment would not match the training draw
            raise ValueError('sampling.regime = "uniform" is not supported for dataset "dcts": dcts runs are selected '
                             'and evaluated with refine\'s k-hop inference (TTA and single subgraphs at '
                             'sampling.khop), which a uniform point subset does not define; use "scatter" or "roi"')
    if c['anchor_cap'] < 0:
        raise ValueError('context.anchor_cap must be >= 0 (0 = no cap)')
    if c['anchor_cap'] and d['name'] != 'dcts':
        # the olbc / r0s evaluation entries (eval_cfg, eval_r0s) do not pass the generator the cap draws from
        raise ValueError('context.anchor_cap is implemented for dataset "dcts" only')
    if d['name'] == 'r0s':
        # beno: allowed since d58 (10-08); in 3-D its records follow a Hilbert curve (zoo/beno.py change 7)
        if d['data_dir'] == DEFAULTS['dataset']['data_dir']:
            raise ValueError('dataset.name = "r0s" needs dataset.data_dir (for example "data/r0s_v1")')
        if c['mode'] == 'decoupled' and ('context', 'anchors') not in explicit:
            # the global default (1024) is olbc's; R0-S decoupled arms state theirs (design: 4096)
            raise ValueError('r0s decoupled runs need an explicit context.anchors (the design uses 4096)')
    else:
        if s['regime'] == 'uniform':
            raise ValueError('sampling.regime = "uniform" is implemented for dataset "r0s" only')
        if c['measure'] == 'area_ht':
            raise ValueError('context.measure = "area_ht" is implemented for dataset "r0s" only')
    if not 0 < b['record_sample'] <= 1:
        raise ValueError('boundary.record_sample must be in (0, 1]')
    if b['types'] != 'kind' and d['name'] != 'dcts' and not (d['name'] == 'r0s' and b['types'] == 'none'):
        raise ValueError(f'boundary.types = "{b["types"]}" is implemented for dataset "dcts" only (roles from the case '
                         'specification; no type at all for the ablation), and "none" also for "r0s"')
    if b['type_encoding'] != 'onehot' and b['types'] != 'kind':
        # text / const_text encode the D / N / R type names; with types = "none" there is no type to encode
        raise ValueError(f'boundary.type_encoding = "{b["type_encoding"]}" encodes the boundary types: it needs '
                         'boundary.types = "kind"')
    if b['bridge_measure'] == 'off' and (m['backbone'] != 'gnot' or b['channel'] == 'none'):
        # c107: GNOT whose own boundary cross-attention gives way to the SCANO channel (the R29 of the c95 tie)
        raise ValueError('boundary.bridge_measure = "off" (no own boundary cross-attention) is implemented for '
                         'gnot with boundary.channel only: the channel is then its boundary route')
    if b['channel_every'] < 1:
        raise ValueError('boundary.channel_every must be >= 1 (1 = the channel in every layer)')
    if b['channel_every'] != 1:
        if m['backbone'] not in PLUGIN_HOSTS | {'slice'}:
            raise ValueError('boundary.channel_every is implemented for the slice backbone and the channel plug-in '
                             '(mgn, gnot, geotransolver) only')
        if b['channel_every'] >= m['depth']:
            raise ValueError('boundary.channel_every >= model.depth reads the channel in the first layer only: use '
                             'boundary.channel = "first_layer"')
    if not 0 <= b['global_dim'] < m['width']:
        raise ValueError('boundary.global_dim must be 0 (no bottleneck) or a code width between 1 and model.width - 1')
    if not 0 <= b['global_dropout'] < 1:
        raise ValueError('boundary.global_dropout must be in [0, 1) (0 = the code always reaches the layers)')
    if b['global_dropout'] and not (b['node_bc'] or b['channel'] != 'none' or b['lfe'] != 'none'):
        # a dropped step would leave the trunk without any boundary information: the dropout can only remove
        # information there, it cannot move the fit onto another path
        raise ValueError('boundary.global_dropout > 0 needs another boundary path for the dropped steps to train '
                         '(boundary.node_bc = true, a channel or the LFE extender)')
    if b['source'] != 'none' and d['name'] != 'dcts':
        raise ValueError('boundary.source is implemented for dataset "dcts" only (the server pairs of DCTS)')
    if m['message_passing']:
        if m['backbone'] != 'slice':
            raise ValueError('model.message_passing is implemented for the slice backbone only (the SCANO trunk)')
        if not 1 <= m['message_passing'] <= 4 or m['width'] % 2:
            raise ValueError('model.message_passing must be 0 (off) or 1-4 GN blocks per layer, with an even width')
        if m['gn_context'] == 'mesh' and d['name'] == 'dcts':
            raise ValueError('model.gn_context = "mesh" is implemented for r0s and olbc only (a whole-mesh GN pass in '
                             'every training step of a ~1M-node DCTS case is not affordable)')
        if s['regime'] == 'uniform' and s['sample_ratio'] < 1 and m['gn_context'] != 'mesh':
            # a uniform point subset keeps a mesh edge only when both ends are drawn (probability rho^2): the GN
            # would train on an almost edgeless graph and deploy on the whole mesh
            raise ValueError('model.message_passing with sampling.regime = "uniform": a uniform subset keeps an edge '
                             'with probability rho^2, so the GN would see almost no edges in training; set '
                             'model.gn_context = "mesh" (the GN on the whole mesh, the loss on the drawn nodes) or '
                             'train without message passing')
    if b['bc_input'] != 't1' and d['name'] != 'dcts':
        raise ValueError('boundary.bc_input = "spec" is implemented for dataset "dcts" only (the cfdrun case specification)')
    if b['outlet_slices'] < 0:
        raise ValueError('boundary.outlet_slices must be >= 0 (0 = off)')
    if b['outlet_slices'] and (d['name'] != 'dcts' or b['types'] != 'role'):
        raise ValueError('boundary.outlet_slices needs dataset "dcts" and boundary.types = "role" (the outlet role marks '
                         'the records)')
    if b['oracle_code'] < 0:
        raise ValueError('boundary.oracle_code must be >= 0 (0 = the route pools the records)')
    if b['oracle_code']:
        if d['name'] != 'r0s':
            raise ValueError('boundary.oracle_code is implemented for dataset "r0s" only')
        if m['backbone'] != 'slice' or b['channel'] != 'none' or b['lfe'] != 'none' or b['records'] != 'face':
            raise ValueError('boundary.oracle_code replaces the records of the global route: it needs the slice '
                             'backbone, channel = "none", lfe = "none" and records = "face"')
        if b['global_dim'] or b['record_sample'] != 1.0:
            raise ValueError('boundary.oracle_code is already a d-dimensional code: global_dim must stay 0 and '
                             'record_sample 1')
        if b['oracle_code'] > 512:
            raise ValueError('boundary.oracle_code: at most 512 modes')
    if b['near_radius'] < 0 or b['near_k'] < 1:
        raise ValueError('boundary.near_radius must be >= 0 (0 = off) and boundary.near_k >= 1')
    if b['near_radius'] and (b['records'] != 'slices' or m['backbone'] not in ('slice', 'abupt')
                             or b['channel'] == 'none'):
        raise ValueError('boundary.near_radius (c102) is the near-field read beside the slice-token read: it needs '
                         'boundary.records = "slices", a channel and backbone slice or abupt')
    if b['records'] == 'slices':
        if m['backbone'] not in {'slice', 'transolver', 'abupt', 'aerogto'} | PLUGIN_HOSTS or b['channel'] == 'none':
            raise ValueError('boundary.records = "slices" is read by the SCANO channel: backbone slice, transolver, '
                             'abupt, aerogto or a plug-in host (mgn, gnot, geotransolver) with boundary.channel '
                             'every_layer or first_layer')
        if b['slices'] < 1 or b['mass_ref'] <= 0:
            raise ValueError('boundary.slices must be >= 1 and boundary.mass_ref > 0')
        if not 0 < b['slice_lse_max'] <= 80:
            # exp(80) ~ 5.5e34 keeps fp32 finite after the value projection; the default is 30
            raise ValueError('boundary.slice_lse_max must be in (0, 80]')
        if b['slice_read'] == 'both' and d['name'] in ('dcts', 'r0s') and ('boundary', 'mass_ref') not in explicit:
            # the unnormalised read is sum_g (M_g / mass_ref) exp(s) v: mass_ref must be of the order of the total
            # record weight (DCTS: about 4.2e3 m^2 under arc; the number of records under count), else the first
            # updates jolt the residual stream
            raise ValueError('boundary.slice_read = "both" on dcts / r0s needs an explicit boundary.mass_ref (the '
                             'typical total record weight, in the unit of the records\' weights)')
    if b.get('global_pool', 'mean') == 'attention' and (b['global_dim'] or b.get('oracle_code', 0)):
        raise ValueError('boundary.global_pool = "attention" does not combine with global_dim or oracle_code')
    if b.get('record_read', 'normalized') == 'both':
        if d['name'] in ('dcts', 'r0s') and ('boundary', 'mass_ref') not in explicit:
            raise ValueError('boundary.record_read = "both" on dcts / r0s needs an explicit boundary.mass_ref '
                             '(the typical total record weight of a case, in the unit of the record weights)')
        if b['mass_ref'] <= 0 or b['channel_measure'] != 'arc':
            raise ValueError('boundary.record_read = "both" needs boundary.mass_ref > 0 and channel_measure = "arc"')
    if not 0.0 <= b['channel_rope_lmin'] < 1.0:
        raise ValueError('boundary.channel_rope_lmin must be in [0, 1) (a share of the coordinate range; 0 = every pair)')
    if b['channel_pos'] != 'raw' and b['records'] == 'slices':
        # rope / host need a position per record; slice tokens have none
        raise ValueError('boundary.channel_pos = "rope" / "host" needs per-record positions (records face or patch), '
                         'not slice tokens')
    if b['records'] == 'patch':
        if d['name'] == 'olbc':
            raise ValueError('boundary.records = "patch" needs per-record patch ids; the olbc records have none')
        if b['record_sample'] < 1:
            # a face subset would give partial-area patch means; subsampling whole patches is not implemented
            raise ValueError('boundary.records = "patch" does not combine with boundary.record_sample < 1')
    if c['measure'] == 'area_ht':
        if c['mode'] != 'coupled' and not (c['anchor_design'] == 'sample' and not c['anchor_ratio']):
            # c125: sampled anchors (a second draw of the k-hop design) take V / pi of that design
            raise ValueError('context.measure = "area_ht" is for coupled contexts (decoupled arms use '
                             'context.anchor_design)')
        if s['regime'] == 'uniform' or s['sample_ratio'] >= 1:
            # every node has the same inclusion probability: V / pi is the area measure
            raise ValueError('context.measure = "area_ht" has no effect under a uniform subset or sample_ratio = 1; '
                             'use "area"')
    if d['name'] in ('dcts', 'olbc') and c['anchor_design'] not in DCTS_ANCHOR_DESIGNS:
        # volume-PPS keeps its probabilities on the data object, which would be the wrong mesh after a case switch (dcts)
        # and reads the cell pool and volumes that olbc's data object does not have
        raise ValueError(f'context.anchor_design = "{c["anchor_design"]}" is implemented for dataset "r0s" only; '
                         f'{d["name"]} takes {sorted(DCTS_ANCHOR_DESIGNS)}')
    if c['anchor_design'] in ('stratified', 'stratified_volume') and c['anchor_cap']:
        # the cap would keep a uniform subset of the anchors and break the one-per-stratum design
        raise ValueError(f'context.anchor_design = "{c["anchor_design"]}" does not combine with context.anchor_cap')
    if c['anchor_design'] in ('volume_pps', 'stratified_volume') and c['measure'] != 'area':
        raise ValueError(f'context.anchor_design = "{c["anchor_design"]}" needs context.measure = "area" '
                         '(the mesh cache at deployment weights by volume)')
    sample_ht = c['measure'] == 'area_ht' and d['name'] == 'r0s' and not c['anchor_ratio']     # c125: V / pi anchors
    if c['anchor_design'] == 'sample' and (c['mode'] != 'decoupled' or s['regime'] not in ('scatter', 'uniform')
                                           or (c['measure'] not in ('area', 'count') and not sample_ht)):
        # c117: a second draw of the nominal sampler (scatter pivots + k-hop, or uniform), weighted like a coupled context
        raise ValueError('context.anchor_design = "sample" needs context.mode = "decoupled", sampling.regime "scatter" '
                         'or "uniform" and context.measure "area" or "count"')
    if not 0.0 <= c['anchor_ratio'] <= 1.0 or (c['anchor_ratio'] and c['anchor_design'] != 'sample'):
        raise ValueError('context.anchor_ratio is a ratio in [0, 1] and is read only with context.anchor_design = '
                         '"sample" (0 = sampling.sample_ratio)')
    if c['anchor_pool'] == 'nodes' and (c['anchor_design'] != 'uniform' or c['measure'] != 'count'):
        # the volume designs and strata are laid out over cells; the area measure gives a boundary face weight 0
        raise ValueError('context.anchor_pool = "nodes" needs context.anchor_design = "uniform" and '
                         'context.measure = "count"')
    # A1 designs (c60; scano.Datasets.design_sampling)
    lw = cfg['train']['loss_weight']
    if s['regime'] == 'partition':
        if d['name'] != 'r0s':
            raise ValueError('sampling.regime = "partition" is implemented for dataset "r0s" only (its boundary faces '
                             'go with their owner cell)')
        if s['sample_ratio'] > 2 / 3:
            raise ValueError('sampling.regime = "partition" needs sample_ratio <= 2/3 (at least two blocks, '
                             'round(1 / sample_ratio)); whole-graph training is regime "scatter" with sample_ratio 1')
    if s['faces'] == 'stratified':
        if s['regime'] not in ('scatter', 'uniform'):
            raise ValueError('sampling.faces = "stratified" is implemented for the regimes "scatter" and "uniform"')
        if s['sample_ratio'] >= 1:
            raise ValueError('sampling.faces = "stratified" has no effect at sample_ratio 1 (every face is drawn)')
    if lw == 'ht':
        if s['regime'] not in ('scatter', 'uniform') or s['sample_ratio'] >= 1:
            raise ValueError('train.loss_weight = "ht" needs a scatter (k-hop) or uniform design with sample_ratio < 1 '
                             '(roi: no analytic pi; partition: every node of the block has pi = 1 / K, so the block '
                             'mean is already the design-unbiased loss)')
        if s['regime'] == 'uniform' and s['faces'] == 'native':
            raise ValueError('train.loss_weight = "ht" has no effect under a uniform subset with native faces (every '
                             'node has pi = budget / N); use "uniform"')
    if (c['measure'] == 'area_ht' or lw in ('v_over_pi', 'inv_pi')) and (s['regime'] == 'partition'
                                                                   or s['faces'] != 'native'):
        raise ValueError('context.measure = "area_ht" and train.loss_weight = "v_over_pi" / "inv_pi" read the '
                         'inclusion files of the native samplers (c43); with an A1 design use train.loss_weight = "ht"')
    if (s['rho_mix'] or s['stratify'] != 'none') and (s['regime'] == 'partition' or s['faces'] != 'native'):
        # the A1 draws (r0s_common.design_nodes) take neither a mixed ratio nor the patch top-up (merge of c60, c61)
        raise ValueError('sampling.rho_mix / sampling.stratify (A8, c61) do not combine with the A1 designs '
                         '(sampling.regime = "partition", sampling.faces = "stratified"; c60)')
    _check_tricks(cfg)
    _check_schedule(cfg, explicit)
    t = cfg['train']
    if t['pair_weight'] < 0:
        raise ValueError('train.pair_weight must be >= 0')
    if bool(d['pairs_dir']) != (t['pair_weight'] > 0):
        raise ValueError('paired-intervention training needs both dataset.pairs_dir and train.pair_weight > 0 '
                         '(neither = off)')
    if d['pairs_dir']:
        if d['name'] != 'dcts':
            raise ValueError('dataset.pairs_dir is implemented for dataset "dcts" only (interventions solved on the base '
                             "room's mesh)")
        if d['pairs_dir'] == d['data_dir']:
            raise ValueError('dataset.pairs_dir must be its own cache (the training-room interventions), not data_dir')
        if c['anchor_cap']:
            # the cap draws a random subset per forward: the room and its pair would see different contexts
            raise ValueError('dataset.pairs_dir does not combine with context.anchor_cap (the pair term needs the '
                             "room's draw: the same nodes and the same context)")
        if t['consistency']:
            # A8's second draw of the room would be a third graph in the step (c74-gn merge: neither implemented
            # nor tested together; train_cfg's paired step holds the room and its pair)
            raise ValueError('dataset.pairs_dir does not combine with train.consistency')
        if b['server_jump'] != 'none':
            # the room's training forward reads the outlet values of its draw (teacher / unrolled), pair_term's forward
            # of the pair would deploy predict's fixed point instead (merge of c74-gn into main, 10-09: neither
            # implemented nor tested together)
            raise ValueError('dataset.pairs_dir does not combine with boundary.server_jump')
    return cfg


def _check_schedule(cfg, explicit):
    """Rules of the learning-rate schedule and weight-averaging keys (decision d59; the step counts are checked by
    train_cfg.lr_factor, which knows the number of training cases)."""
    tr = cfg['train']
    if not 0 <= tr['lr_min_ratio'] < 1:
        raise ValueError('train.lr_min_ratio must be in [0, 1) (the floor as a fraction of train.lr)')
    if not 0 <= tr['warmup_frac'] < 1:
        raise ValueError('train.warmup_frac must be in [0, 1) (0 = the warm-up rule min(warmup_max, steps // 10 + 1))')
    if tr['warmup_frac'] and ('train', 'warmup_max') in explicit:
        raise ValueError('train.warmup_frac and train.warmup_max both set: a variant states its warm-up once '
                         '(warmup_frac > 0 replaces the rule min(warmup_max, steps // 10 + 1))')
    if not 0 < tr['cooldown_frac'] < 1:
        raise ValueError('train.cooldown_frac must be in (0, 1)')
    if not 0 <= tr['ema_decay'] < 1:
        raise ValueError('train.ema_decay must be in [0, 1) (0 = no weight average)')


def _check_tricks(cfg):
    """Rules of the A8 training-trick keys (sampling.rho_mix / stratify, boundary.node_bc_mask, train.consistency)."""
    s, b, tr = cfg['sampling'], cfg['boundary'], cfg['train']
    mix = s['rho_mix']
    if mix:
        if not all(0 < r <= 1 for r in mix):
            raise ValueError(f'sampling.rho_mix = {mix}: every ratio must be in (0, 1]')
        if s['rho_mix_draw'] == 'set':
            if len(mix) < 2 or len(set(mix)) != len(mix):
                raise ValueError('sampling.rho_mix with rho_mix_draw = "set" needs at least two distinct ratios')
        elif len(mix) != 2 or not mix[0] < mix[1]:
            raise ValueError('sampling.rho_mix with rho_mix_draw = "log_uniform" is [lo, hi] with lo < hi')
        if not min(mix) <= s['sample_ratio'] <= max(mix):
            raise ValueError(f"sampling.sample_ratio = {s['sample_ratio']} (the nominal ratio that validation, "
                             f'selection and evaluation deploy) must lie within sampling.rho_mix = {mix}')
    if s['stratify'] != 'none' and s['sample_ratio'] >= 1 and not mix:
        raise ValueError('sampling.stratify has no effect at sample_ratio 1 (every face is in the draw)')
    if (mix or s['stratify'] != 'none') and (cfg['context']['measure'] == 'area_ht' or tr['loss_weight'] != 'uniform'):
        # pi is the inclusion probability of the nominal sampler (c43's files); a mixed or stratified design has others
        raise ValueError('context.measure = "area_ht" and train.loss_weight = "v_over_pi" / "inv_pi" use the inclusion '
                         'probabilities of the nominal sampler; they do not combine with sampling.rho_mix or '
                         'sampling.stratify')
    if not 0 <= b['node_bc_mask'] < 1:
        raise ValueError('boundary.node_bc_mask must be in [0, 1)')
    if tr['consistency'] < 0:
        raise ValueError('train.consistency must be >= 0')
    if b['server_jump'] != 'none':
        if not 0.0 < b['server_jump_p'] <= 1.0:
            raise ValueError('boundary.server_jump_p must be in (0, 1]')
        if not 2 <= b['server_jump_passes'] <= 5:
            raise ValueError('boundary.server_jump_passes must be 2..5 (pass 1 reads the cached outlet values)')
        if not b['server_jump_tol'] > 0.0:
            raise ValueError('boundary.server_jump_tol must be > 0 (kelvin)')
        if b['records'] == 'patch':
            raise ValueError('boundary.server_jump sets the outlet face records; patch records aggregate them')
    if b['server_route'] != 'none':
        m = cfg['model']
        if (m['backbone'] != 'slice' or b['records'] != 'slices' or b['channel'] == 'none'
                or b.get('slice_assign', 'learned') != 'learned' or b.get('outlet_slices', 0)):
            raise ValueError('boundary.server_route needs the slice backbone with a channel reading learned slice tokens '
                             '(boundary.records = "slices", slice_assign "learned", no outlet_slices)')
    if tr['zone_weight'] < 1.0:
        raise ValueError('train.zone_weight must be >= 1 (1 = off; the server zone is up-weighted, never down)')
    if tr['zone_radius'] <= 0.0:
        raise ValueError('train.zone_radius must be > 0 (metres)')


# 10-14 (owner): the data set GTC-million was renamed DCTS. Runs trained before record dataset.name = "gtc" (and data
# paths under data/GTC-million, which stay as links); canonical() reads such a configuration with the current name.
DATASET_ALIASES = {'gtc': 'dcts'}


def canonical(cfg):
    """`cfg`, a resolved run configuration (from a variant TOML, a checkpoint or a run's config.json), with the current
    data-set name; changed in place and returned."""
    ds = cfg.get('dataset') if isinstance(cfg, dict) else None
    if isinstance(ds, dict) and ds.get('name') in DATASET_ALIASES:
        ds['name'] = DATASET_ALIASES[ds['name']]
    return cfg


def load(path) -> dict:
    with open(path, 'rb') as f:
        return resolve(tomllib.load(f))


def _before_rename(v):
    """A configuration value as it was written before the data set was renamed DCTS (10-14), so that a protocol
    keeps its fingerprint across the rename: the data-set name and the data paths."""
    if isinstance(v, str):
        if v == 'dcts':
            return 'gtc'
        return v.replace('data/DCTS/', 'data/GTC-million/').replace('dcts_cache', 'gtc_cache')
    if isinstance(v, list):
        return [_before_rename(x) for x in v]
    if isinstance(v, dict):
        return {k: _before_rename(x) for k, x in v.items()}
    return v


def fingerprint(cfg: dict) -> str:
    """Hash of the protocol: the resolved configuration without the seed (data-set name and data paths as written
    before the rename to DCTS, so that every protocol keeps its fingerprint)."""
    cfg = _before_rename(cfg)
    body = {sec: {k: v for k, v in d.items()
                  if (sec, k) not in NOT_IN_FINGERPRINT and ADDED_LATER.get((sec, k), object()) != v}
            for sec, d in cfg.items()}
    body = {sec: d for sec, d in body.items() if d}             # a section added later and left at defaults
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]


def describe(cfg: dict) -> str:
    """One line for logs: the fields that most often differ between variants."""
    b, c, s, m = cfg['boundary'], cfg['context'], cfg['sampling'], cfg['model']
    every = b.get('channel_every', 1)
    code = b.get('global_dim', 0)
    pos = f"/{b['channel_pos']}" if b.get('channel_pos', 'raw') != 'raw' else ''        # abupt read positions (c75)
    pos += f"/lmin{b['channel_rope_lmin']:g}" if b.get('channel_rope_lmin', 0.0) else ''   # c98: the read's RoPE floor
    parts = ['node_bc' if b['node_bc'] else '',
             f"channel:{b['channel']}/{b['channel_measure']}{pos}" + (f'/every{every}' if every != 1 else '')
             + ('/dinit' if b.get('channel_init', 'host') == 'default' else '')
             if b['channel'] != 'none' else '',
             f"global:{b['global']}/{b['global_measure']}" + (f'/d{code}' if code else '')
             + ('/attn' if b.get('global_pool', 'mean') == 'attention' else '')
             + (f"/oracle{b['oracle_code']}" if b.get('oracle_code', 0) else '')
             + (f"/drop{b['global_dropout']:g}" if b.get('global_dropout', 0.0) else '')
             if b['global'] != 'none' else '',
             f"lfe:{b['lfe']}/{b['lfe_measure']}" if b['lfe'] != 'none' else '',
             f"{m['backbone']}-bridge/{b['bridge_measure']}" if m['backbone'] in ('sca', 'gnot') else '',
             f"gale-ctx/{b['bridge_measure']}" if m['backbone'] == 'geotransolver' else '',
             'beno-records/max' if m['backbone'] == 'beno' else '']
    route = '+'.join(p for p in parts if p) or 'no-bc'
    hop = '' if s['regime'] == 'uniform' else f" khop{s['khop']}"
    rec = f" rec{b['record_sample']:g}" if b.get('record_sample', 1.0) != 1.0 else ''
    pps = {'volume_pps': '/pps', 'stratified': '/strat', 'stratified_volume': '/vstrat',
           'sample': '/sample'}.get(c.get('anchor_design', 'uniform'), '')
    pps += f"{c['anchor_ratio']:g}" if c.get('anchor_ratio', 0.0) else ''
    pps += f"/cap{c['anchor_cap']}" if c.get('anchor_cap', 0) else ''
    pps += '/nodes' if c.get('anchor_pool', 'cells') == 'nodes' else ''
    lw = cfg['train'].get('loss_weight', 'uniform')
    loss = {'uniform': '', 'v_over_pi': ' | loss v/pi', 'inv_pi': ' | loss 1/pi',
            'ht': ' | loss HT 1/pi'}[lw]          # ht: A1 (c60); inv_pi: A8 (c61)
    hop += f" faces:{s['faces']}" if s.get('faces', 'native') != 'native' else ''
    hop += f" halo{s['halo']}" if s.get('halo', 0) else ''
    hop += f" (K{max(1, int(1 / s['sample_ratio'] + 0.5))} blocks, block loss)" if s['regime'] == 'partition' else ''
    rec += ' per-patch' if b.get('records', 'face') == 'patch' else ''
    rec += f" records/both/ref{b['mass_ref']:g}" if b.get('record_read', 'normalized') == 'both' else ''
    if b.get('records', 'face') == 'slices':
        rec += f" slices{b['slices']}/{b['slice_read']}" + (f"/ref{b['mass_ref']:g}" if b['slice_read'] == 'both' else '')
        rec += '/spatial' if b.get('slice_assign', 'learned') == 'spatial' else ''
        rec += f"/near{b['near_radius']:g}k{b['near_k']}" if b.get('near_radius', 0.0) else ''
        lse = b.get('slice_lse_max', 30.0)
        rec += f'/lse{lse:g}' if b['slice_read'] == 'both' and lse != 30.0 else ''
    rec += f" types:{b['types']}" if b.get('types', 'kind') != 'kind' else ''
    rec += f" types:{b['type_encoding']}" if b.get('type_encoding', 'onehot') != 'onehot' else ''
    rec += (f" source:{b['source']}" + (f"/{b['source_measure']}-measure" if b.get('source_measure', 'area') != 'area'
                                        else '')
            if b.get('source', 'none') != 'none' else '')
    rec += f" outlet_slices{b['outlet_slices']}" if b.get('outlet_slices', 0) else ''
    rec += ' bc:spec' if b.get('bc_input', 't1') != 't1' else ''
    rec += f" bcmask{b['node_bc_mask']:g}" if b.get('node_bc_mask', 0.0) else ''
    jump = b.get('server_jump', 'none')               # B1 / B2 of docs/dcts_inlets_2026-10-08.md (dcts)
    if jump != 'none':
        rec += (f' jump:{jump}' + (f"/p{b['server_jump_p']:g}" if jump == 'teacher' else '')
                + f"/passes{b['server_jump_passes']}/tol{b['server_jump_tol']:g}K")
    rec += f" route:{b['server_route']}" if b.get('server_route', 'none') != 'none' else ''
    loss += ' | tf32' if cfg['train'].get('matmul', 'fp32') == 'tf32' else ''
    loss += f" | consistency {cfg['train']['consistency']:g}" if cfg['train'].get('consistency', 0.0) else ''
    zw = cfg['train'].get('zone_weight', 1.0)
    loss += f" | server zone x{zw:g}/{cfg['train'].get('zone_radius', 0.3):g}m" if zw != 1.0 else ''
    t = cfg['train']
    if cfg['dataset'].get('pairs_dir', ''):
        loss += (f" | pairs:{t['pair_loss']}" + (f"/{t['pair_fields']}" if t['pair_loss'] == 'diff' else '')
                 + f"x{t['pair_weight']:g}")
    loss += ' | checkpoint:always' if t.get('act_checkpoint', 'paired') == 'always' else ''
    mix = s.get('rho_mix', [])
    if mix:
        hop += (f" mix{{{','.join(f'{r:g}' for r in mix)}}}" if s['rho_mix_draw'] == 'set'
                else f' mix[{mix[0]:g},{mix[1]:g}]log')
    hop += ' stratified' if s.get('stratify', 'none') != 'none' else ''
    gn = (f" gn{m['message_passing']}" + ('/mesh' if m.get('gn_context', 'subgraph') == 'mesh' else '')
          if m.get('message_passing', 0) else '')
    gn += f" t{m['slices']}" if m['backbone'] == 'aerogto' else ''      # its learned query tokens (d58)
    gn += '/authinit' if m.get('aerogto_init', 'aaai') == 'author' else ''    # the authors' host init (c81 A1)
    gn += '/domainbox' if m.get('aerogto_box', 'sample') == 'domain' else ''  # fixed-domain normalisation (c95)
    return (f"{m['backbone']} w{m['width']} d{m['depth']}{gn} | route {route}{rec} | ctx {c['mode']}/{c['measure']}/"
            f"{c['eps']}{pps} | sample {s['regime']} rho{s['sample_ratio']:g}{hop}"
            f" | lr {cfg['train']['lr']:g} e{cfg['train']['epochs']}{loss}"
            + (' | rel-L2 loss' if cfg['train'].get('loss', 'mse') == 'rel_l2' else '') + _describe_schedule(cfg['train']))


def _describe_schedule(tr):
    """The describe suffix of the d59 schedule keys: '' at their defaults (every earlier describe string unchanged)."""
    sched = tr.get('schedule', 'cosine')
    parts = []
    if sched != 'cosine':
        parts.append(sched + (f"/cool{tr['cooldown_frac']:g}" if sched == 'wsd' else ''))
    if tr.get('warmup_frac', 0.0):
        parts.append(f"warm{tr['warmup_frac']:g}")
    if tr.get('lr_min_ratio', 0.0):
        parts.append(f"min{tr['lr_min_ratio']:g}")
    out = (' | sched ' + ' '.join(parts)) if parts else ''
    return out + (f" | ema{tr['ema_decay']:g}" if tr.get('ema_decay', 0.0) else '')


def example_toml(path: Path):
    """Write a template listing every key at its default, commented out: uncomment only what the variant
    changes (a key that has no effect under the variant's configuration is rejected when set)."""
    lines = ['# Every key is optional; omitted keys take these defaults (scano.Utils.run_config.DEFAULTS).',
             '# Uncomment only the keys the variant changes.']
    for sec, d in DEFAULTS.items():
        lines.append(f'\n[{sec}]')
        for k, v in d.items():
            val = json.dumps(v) if not isinstance(v, bool) else str(v).lower()
            choices = CHOICES.get((sec, k))
            lines.append(f'# {k} = {val}' + (f'   ({" | ".join(sorted(choices))})' if choices else ''))
    Path(path).write_text('\n'.join(lines) + '\n')
