from __future__ import annotations

import torch
from torch import nn

from .attention_pooling import MaskedAttentionPool


class GRUAttentionRegressor(nn.Module):
    def __init__(
        self,
        n_channels: int,
        n_outputs: int,
        hidden_size: int = 128,
        num_layers: int = 2,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.gru = nn.GRU(
            input_size=n_channels,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.pool = MaskedAttentionPool(hidden_size, dropout=dropout)
        self.head = nn.Linear(hidden_size, n_outputs)

    def forward(self, x: torch.Tensor, attention_mask: torch.Tensor | None = None) -> torch.Tensor:
        sequence = x.transpose(1, 2)
        hidden, _ = self.gru(sequence)
        pooled, _weights = self.pool(hidden, attention_mask=attention_mask)
        return self.head(pooled)
