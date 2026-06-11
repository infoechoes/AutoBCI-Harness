from __future__ import annotations

import torch
from torch import nn

from .attention_pooling import MaskedAttentionPool
from .tcn_regressor import TCNBlock, TCNBlockConfig


class TCNAttentionRegressor(nn.Module):
    def __init__(
        self,
        n_channels: int,
        n_outputs: int,
        hidden_size: int = 128,
        num_layers: int = 2,
        dropout: float = 0.1,
        kernel_size: int = 3,
    ) -> None:
        super().__init__()
        self.input_proj = nn.Conv1d(n_channels, hidden_size, kernel_size=1)
        block_count = max(1, int(num_layers))
        configs = [
            TCNBlockConfig(
                hidden_size=hidden_size,
                kernel_size=kernel_size,
                dilation=2 ** block_idx,
                dropout=dropout,
            )
            for block_idx in range(block_count)
        ]
        self.blocks = nn.ModuleList([TCNBlock(hidden_size, config) for config in configs])
        self.pool = MaskedAttentionPool(hidden_size, dropout=dropout)
        self.head = nn.Linear(hidden_size, n_outputs)

    def forward(self, x: torch.Tensor, attention_mask: torch.Tensor | None = None) -> torch.Tensor:
        hidden = self.input_proj(x)
        for block in self.blocks:
            hidden = block(hidden)
        sequence = hidden.transpose(1, 2)
        pooled, _weights = self.pool(sequence, attention_mask=attention_mask)
        return self.head(pooled)
