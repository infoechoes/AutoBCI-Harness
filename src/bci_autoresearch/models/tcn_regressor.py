from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn
import torch.nn.functional as F


class CausalConv1d(nn.Module):
    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        *,
        kernel_size: int,
        dilation: int = 1,
        bias: bool = True,
    ) -> None:
        super().__init__()
        if kernel_size <= 0:
            raise ValueError("kernel_size must be positive.")
        if dilation <= 0:
            raise ValueError("dilation must be positive.")
        self.left_padding = (kernel_size - 1) * dilation
        self.conv = nn.Conv1d(
            in_channels=in_channels,
            out_channels=out_channels,
            kernel_size=kernel_size,
            dilation=dilation,
            padding=0,
            bias=bias,
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.left_padding > 0:
            x = F.pad(x, (self.left_padding, 0))
        return self.conv(x)


@dataclass(frozen=True)
class TCNBlockConfig:
    hidden_size: int
    kernel_size: int
    dilation: int
    dropout: float


class TCNBlock(nn.Module):
    def __init__(self, in_channels: int, config: TCNBlockConfig) -> None:
        super().__init__()
        self.conv1 = CausalConv1d(
            in_channels,
            config.hidden_size,
            kernel_size=config.kernel_size,
            dilation=config.dilation,
        )
        self.norm1 = nn.GroupNorm(1, config.hidden_size)
        self.conv2 = CausalConv1d(
            config.hidden_size,
            config.hidden_size,
            kernel_size=config.kernel_size,
            dilation=config.dilation,
        )
        self.norm2 = nn.GroupNorm(1, config.hidden_size)
        self.dropout = nn.Dropout(config.dropout) if config.dropout > 0.0 else nn.Identity()
        self.residual = (
            nn.Conv1d(in_channels, config.hidden_size, kernel_size=1)
            if in_channels != config.hidden_size
            else nn.Identity()
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        residual = self.residual(x)
        out = self.conv1(x)
        out = self.norm1(out)
        out = F.gelu(out)
        out = self.dropout(out)
        out = self.conv2(out)
        out = self.norm2(out)
        out = F.gelu(out)
        out = self.dropout(out)
        return F.gelu(out + residual)


class TCNRegressor(nn.Module):
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
        self.readout_dropout = nn.Dropout(dropout) if dropout > 0.0 else nn.Identity()
        self.head = nn.Linear(hidden_size, n_outputs)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.input_proj(x)
        for block in self.blocks:
            h = block(h)
        last = self.readout_dropout(h[:, :, -1])
        return self.head(last)
