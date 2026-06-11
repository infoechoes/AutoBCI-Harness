from __future__ import annotations

import torch
from torch import nn


class GRURegressor(nn.Module):
    def __init__(
        self,
        n_channels: int,
        n_outputs: int,
        hidden_size: int = 128,
        num_layers: int = 2,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.readout_dropout = nn.Dropout(dropout) if dropout > 0.0 else nn.Identity()
        self.gru = nn.GRU(
            input_size=n_channels,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.head = nn.Linear(hidden_size, n_outputs)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, C, T) -> (B, T, C)
        x = x.transpose(1, 2)
        out, _ = self.gru(x)
        last = self.readout_dropout(out[:, -1, :])
        return self.head(last)
