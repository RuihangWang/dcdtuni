import torch
import torch.nn as nn
from torch_geometric.nn import MessagePassing
from torch_scatter import scatter_add, scatter_mean



def build_mlp_from_num_layer(
    in_size,
    hidden_size,
    out_size,
    drop_out=False,
    lay_norm=True,
    dropout_prob=0.2,
    num_layer=2,
):
    layers = []
    layers.append(nn.Linear(in_size, hidden_size))
    if drop_out:
        layers.append(nn.Dropout(p=dropout_prob))
    layers.append(nn.GELU())

    # Add specified number of hidden layers
    for _ in range(num_layer - 1):
        layers.append(nn.Linear(hidden_size, hidden_size))
        if drop_out:
            layers.append(nn.Dropout(p=dropout_prob))
        layers.append(nn.GELU())

    layers.append(nn.Linear(hidden_size, out_size))

    if lay_norm:
        layers.append(nn.LayerNorm(normalized_shape=out_size))

    return nn.Sequential(*layers)


class OptimizedNodeBlock(MessagePassing):
    def __init__(self, hidden_size, custom_func=None, aggr='add'):
        super(OptimizedNodeBlock, self).__init__(aggr=aggr)
        self.net = custom_func
        self.hidden_size = hidden_size

    def forward(self, x, edge_index, edge_attr):
        senders_node_idx, receivers_node_idx = edge_index

        twoway_node_connections_indegree = torch.cat(
            [senders_node_idx, receivers_node_idx], dim=0
        )
        twoway_node_connections_outdegree = torch.cat(
            [receivers_node_idx, senders_node_idx], dim=0
        )

        twoway_edge_attr = torch.cat(torch.chunk(edge_attr, 2, dim=-1), dim=0)

        node_agg_received_edges = scatter_add(
            twoway_edge_attr,
            twoway_node_connections_indegree,
            dim=0,
            dim_size=x.size(0)
        )

        node_avg_neighbor_node = scatter_mean(
            node_agg_received_edges[twoway_node_connections_outdegree],
            twoway_node_connections_indegree,
            dim=0,
            dim_size=x.size(0)
        )

        updated_x = self.net(torch.cat([node_avg_neighbor_node, x], dim=1))
        return updated_x


class OptimizedEdgeBlock(MessagePassing):
    def __init__(self, hidden_size, custom_func=None, aggr='add'):
        super(OptimizedEdgeBlock, self).__init__(aggr=aggr)
        self.net = custom_func
        self.hidden_size = hidden_size

    def forward(self, x, edge_index, edge_attr):
        senders_node_idx, receivers_node_idx = edge_index

        twoway_node_connections_indegree = torch.cat(
            [senders_node_idx, receivers_node_idx], dim=0
        )
        twoway_node_connections_outdegree = torch.cat(
            [receivers_node_idx, senders_node_idx], dim=0
        )

        node_avg_neighbor_node = scatter_add(
            x[twoway_node_connections_outdegree],
            twoway_node_connections_indegree,
            dim=0,
            dim_size=x.size(0)
        )

        senders_attr = node_avg_neighbor_node[senders_node_idx]
        receivers_attr = node_avg_neighbor_node[receivers_node_idx]

        collected_edges = torch.cat([senders_attr, receivers_attr, edge_attr], dim=1)
        updated_edge_attr = self.net(collected_edges)

        return updated_edge_attr


class OptimizedEncoder(nn.Module):
    def __init__(
        self,
        node_input_size=128,
        edge_input_size=128,
        hidden_size=128,
    ):
        super(OptimizedEncoder, self).__init__()

        # hidden_size check, can not be odd number
        if hidden_size % 2 != 0:
            raise ValueError("hidden_size must be even number")

        self.nb_encoder = build_mlp_from_num_layer(
            node_input_size, hidden_size, int(hidden_size), drop_out=False, lay_norm=True,
        )
        self.eb_encoder = build_mlp_from_num_layer(
            edge_input_size, hidden_size, int(hidden_size/2), drop_out=False, lay_norm=True,
        )

    def forward(self, x, edge_attr):

        node_features = self.nb_encoder(x)

        reverse_edge_features = torch.cat((-edge_attr[:,:-1], edge_attr[:,-1:]), dim=1)
        twoway_edge_attr = torch.cat((edge_attr, reverse_edge_features), dim=0)

        twoway_edge_features = self.eb_encoder(twoway_edge_attr)

        edge_features = torch.cat((torch.chunk(twoway_edge_features, 2, dim=0)), dim=1)

        return node_features, edge_features


class OptimizedGnBlock(nn.Module):
    def __init__(self, hidden_size=128, drop_out=False, dtype=torch.float32):
        super(OptimizedGnBlock, self).__init__()
        
        eb_input_dim = int(3 * hidden_size)
        nb_input_dim = int(hidden_size + (hidden_size // 2.0))
        
        self.nb_module = OptimizedNodeBlock(
            hidden_size,
            custom_func=build_mlp_from_num_layer(
                nb_input_dim, hidden_size, int(hidden_size), drop_out=drop_out, lay_norm=True
            )
        )
        
        self.eb_module = OptimizedEdgeBlock(
            hidden_size,
            custom_func=build_mlp_from_num_layer(
                eb_input_dim, hidden_size, int(hidden_size), drop_out=drop_out, lay_norm=True
            )
        )
        
    def forward(self, x, edge_index, edge_attr):
        # Keep GN scatter/index updates in FP32.
        # This path is numerically safer and avoids compile+autocast failures
        # on batched graphs.
        x_fp32 = x.float()
        edge_attr_fp32 = edge_attr.float()

        updated_edge_attr = self.eb_module(x_fp32, edge_index, edge_attr_fp32)
        updated_x = self.nb_module(x_fp32, edge_index, updated_edge_attr)

        x_out = x_fp32 + updated_x
        edge_attr_out = edge_attr_fp32 + updated_edge_attr

        return x_out.to(x.dtype), edge_attr_out.to(edge_attr.dtype)


class OptimizedDecoder(nn.Module):
    def __init__(
        self,
        hidden_size=128,
        node_output_size=3,
    ):
        super(OptimizedDecoder, self).__init__()
        
        self.node_decode_module = build_mlp_from_num_layer(
            hidden_size,
            hidden_size,
            node_output_size,
            drop_out=False,
            lay_norm=False,
            num_layer=2,
        )
        
    def forward(self, x):
        return self.node_decode_module(x)


class OptimizedEncoderProcessorDecoder(nn.Module):
    def __init__(
        self,
        message_passing_num,
        edge_input_size,
        node_input_size,
        node_output_size,
        drop_out=False,
        hidden_size=128,
        params=None,
    ):
        super(OptimizedEncoderProcessorDecoder, self).__init__()
        
        self.encoder = OptimizedEncoder(
            node_input_size=node_input_size,
            edge_input_size=edge_input_size,
            hidden_size=hidden_size,
        )
        
        self.gn_blocks = nn.ModuleList([
            OptimizedGnBlock(
                hidden_size=hidden_size,
                drop_out=drop_out,
            ) for _ in range(message_passing_num)
        ])
        
        self.decoder = OptimizedDecoder(
            hidden_size=hidden_size,
            node_output_size=node_output_size,
        )
        
    def forward(self, graph_node):
        x, edge_index, edge_attr = graph_node.x, graph_node.edge_index, graph_node.edge_attr

        x, edge_attr = self.encoder(x, edge_index, edge_attr)

        for gn_block in self.gn_blocks:
            x, edge_attr = gn_block(x, edge_index, edge_attr)

        pred_node = self.decoder(x)
        return pred_node
