from __future__ import annotations

import torch
from torch import nn
import torch.nn.functional as F


class _CausalConv1d(nn.Module):
    def __init__(self, in_channels: int, out_channels: int, *, kernel_size: int) -> None:
        super().__init__()
        if kernel_size <= 0:
            raise ValueError("kernel_size must be positive.")
        self.left_padding = kernel_size - 1
        self.conv = nn.Conv1d(
            in_channels=in_channels,
            out_channels=out_channels,
            kernel_size=kernel_size,
            padding=0,
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.left_padding > 0:
            x = F.pad(x, (self.left_padding, 0))
        return self.conv(x)


class CNNLSTMRegressor(nn.Module):
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
        conv_hidden = max(16, hidden_size // 2)
        self.feature_extractor = nn.Sequential(
            _CausalConv1d(n_channels, conv_hidden, kernel_size=kernel_size),
            nn.GELU(),
            _CausalConv1d(conv_hidden, hidden_size, kernel_size=kernel_size),
            nn.GELU(),
        )
        self.lstm = nn.LSTM(
            input_size=hidden_size,
            hidden_size=hidden_size,
            num_layers=max(1, int(num_layers)),
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.readout_dropout = nn.Dropout(dropout) if dropout > 0.0 else nn.Identity()
        self.head = nn.Linear(hidden_size, n_outputs)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.feature_extractor(x).transpose(1, 2)
        out, _ = self.lstm(h)
        last = self.readout_dropout(out[:, -1, :])
        return self.head(last)
