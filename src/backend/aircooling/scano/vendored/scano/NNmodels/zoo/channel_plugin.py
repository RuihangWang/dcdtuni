"""The SCANO boundary channel as a plug-in (decisions d62 / d63, host matrix c95): hosts without a channel of their
own (GNOT, GeoTransolver, MeshGraphNets) read the boundary records after every host block (or after every
channel_every-th one, or after the first only):

    x <- x + Read_i(LN_i(x), lift(records), log w)

Read is the per-record attention of slice_operator.BoundaryAttention (softmax with the log-weight bias: arc = area
weights as a quadrature, count = no bias), or the read of the area-consistent slice tokens of the records
(boundary.records = "slices"; area_slices.SliceTokenRead), exactly as the Transolver and AB-UPT hosts read them.
Every module is created after the host's parameters, so a host with the plug-in has the native host's parameters at
the same seed. Initialisation: PyTorch's default (the Transolver channel adopted it in c84), with the unnormalised
read's output at zero, so a fresh plug-in starts as a small perturbation of the native host.
"""
from __future__ import annotations

import torch
import torch.nn as nn

from .slice_operator import _slicer, mlp, slice_tokens
from .transolver import _channel_read, _rezero_unnormalised


def plugin_layers(channel, depth, every=1):
    """Host blocks after which the channel is read: every_layer (every `every`-th block, from 0), first_layer, none."""
    if channel == 'none':
        return []
    if channel == 'first_layer':
        return [0]
    if channel != 'every_layer':
        raise ValueError(f'channel must be none, first_layer or every_layer, got {channel!r}')
    if every < 1:
        raise ValueError('channel_every must be >= 1')
    return [i for i in range(depth) if i % every == 0]


class ChannelPlugin(nn.Module):
    def __init__(self, width, heads, bnd_in, layers, channel_measure='arc', record_slices=None):
        super().__init__()
        if channel_measure not in ('arc', 'count'):
            raise ValueError(f'channel_measure must be arc or count, got {channel_measure!r}')
        self.channel_measure = channel_measure
        self.layers = list(layers)
        self.channels = nn.ModuleDict({str(i): nn.ModuleDict({'ln': nn.LayerNorm(width),
                                                              'boundary': _channel_read(width, heads, record_slices)})
                                       for i in self.layers})
        self.blift = mlp(bnd_in, width, width)
        _rezero_unnormalised(self.channels)
        self.slicer = _slicer(record_slices, width, heads)          # created last, as in the other hosts

    @property
    def needs_record_area(self):
        return getattr(self.slicer, 'outlet', False)

    def tokens(self, bnd_feat, bnd_w, bnd_area=None):
        """(record tokens [B,NB|G,W], log weights [B,NB|G]) of the case, once per forward."""
        bt = self.blift(bnd_feat)
        if self.slicer is not None:                                # boundary.records = "slices"
            return slice_tokens(self.slicer, bt, bnd_w, self.channel_measure, bnd_feat, bnd_area)
        log_w = (torch.log(bnd_w / bnd_w.mean(-1, keepdim=True)) if self.channel_measure == 'arc'
                 else torch.zeros_like(bnd_w))
        return bt, log_w

    def read(self, i, x, bt, log_w):
        """x after host block i, plus the channel's read when block i reads it (x [B,N,W])."""
        ch = self.channels[str(i)] if str(i) in self.channels else None
        if ch is None:
            return x
        return x + ch['boundary'](ch['ln'](x), bt, log_w)
