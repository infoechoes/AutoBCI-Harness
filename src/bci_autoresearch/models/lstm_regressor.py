from __future__ import annotations

import torch
from torch import nn


class LSTMRegressor(nn.Module):
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
        self.lstm = nn.LSTM(
            input_size=n_channels,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.head = nn.Linear(hidden_size, n_outputs)
        self._init_recurrent_biases()

    def _init_recurrent_biases(self) -> None:
        for names in self.lstm._all_weights:
            for name in filter(lambda item: "bias" in item, names):
                bias = getattr(self.lstm, name)
                hidden_size = bias.shape[0] // 4
                with torch.no_grad():
                    bias[hidden_size:2 * hidden_size].fill_(1.0)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, C, T) -> (B, T, C)
        x = x.transpose(1, 2)
        out, _ = self.lstm(x)
        last = self.readout_dropout(out[:, -1, :])
        return self.head(last)
