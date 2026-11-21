import sys
import os
file_dir = os.path.dirname(__file__)
sys.path.append(file_dir)
import torch
import torch.nn as nn
from .EncoderProcesserDecoder import OptimizedGnBlock, OptimizedEncoder, OptimizedDecoder


class Processor(nn.Module):
    def __init__(self,message_passing_num=0, hidden_size=128, drop_out=False):
        super(Processor, self).__init__()

        if message_passing_num<1:
            raise ValueError("message_passing_num must be greater than 0")
        
        GN_block_list = []
        for _ in range(message_passing_num):
            GN_block_list.append(
                OptimizedGnBlock(
                    hidden_size=hidden_size,
                    drop_out=drop_out,
                )
            )
        self.GN_block_list = nn.ModuleList(GN_block_list)


    def forward(self, x, edge_index, edge_attr, batch=None):
        """
        Forward pass through GN blocks.

        Args:
            x: Node features
            edge_index: Edge connectivity
            edge_attr: Edge attributes
            batch: Batch indices (optional)

        Returns:
            Tuple of (updated_x, updated_edge_attr)
        """
        # Process through GN blocks
        for model in self.GN_block_list:
            x, edge_attr = model(x, edge_index, edge_attr)

        return x, edge_attr


class FVGN(nn.Module):
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
        super(FVGN, self).__init__()

        self.encoder = OptimizedEncoder(
            node_input_size=node_input_size,
            edge_input_size=edge_input_size,
            hidden_size=hidden_size,
        )

        self.processer = Processor(
                    message_passing_num=message_passing_num, 
                    hidden_size=hidden_size, 
                    drop_out=False
                )

        self.decoder = OptimizedDecoder(
            hidden_size=hidden_size,
            node_output_size=node_output_size,
        )

        self.node_output_size = node_output_size

    @torch.compile(mode="default", dynamic=True)  # 禁用: Triton不支持Blackwell架构(sm_121a)
    def forward(
        self,
        x,
        edge_attr,
        edge_index,
        batch,
        mask_output_cell=None,
    ):
        # Optimized encoding - returns tensors directly
        x, edge_attr = self.encoder(
            x,
            edge_attr
        )

        # Process through attention processors
        x, edge_attr = self.processer(x, edge_index, edge_attr, batch)

        x = x.to(torch.float32)
        edge_attr = edge_attr.to(torch.float32)

        ''' collected cell scheme'''
        if mask_output_cell is None:
            pred_cell = self.decoder(x)
        else:
            pred_cell = torch.zeros((x.shape[0],self.node_output_size), device=x.device, dtype=torch.float32)
            decoder_output = self.decoder(x[mask_output_cell])
            # Ensure dtype matches for mixed precision training
            pred_cell[mask_output_cell] = decoder_output.to(pred_cell.dtype)

        return pred_cell