from __future__ import annotations

import math

import torch
from torch import nn
import torch.nn.functional as F


class _CausalDepthwiseConv1d(nn.Module):
    def __init__(self, channels: int, kernel_size: int) -> None:
        super().__init__()
        if kernel_size <= 0:
            raise ValueError("kernel_size must be positive.")
        self.left_padding = kernel_size - 1
        self.conv = nn.Conv1d(
            channels,
            channels,
            kernel_size=kernel_size,
            groups=channels,
            padding=0,
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.left_padding > 0:
            x = F.pad(x, (self.left_padding, 0))
        return self.conv(x)


class _CausalSelfAttention(nn.Module):
    def __init__(self, hidden_size: int, num_heads: int, dropout: float) -> None:
        super().__init__()
        if hidden_size % num_heads != 0:
            raise ValueError("hidden_size must be divisible by num_heads.")
        self.num_heads = num_heads
        self.head_dim = hidden_size // num_heads
        self.scale = 1.0 / math.sqrt(self.head_dim)
        self.qkv = nn.Linear(hidden_size, hidden_size * 3)
        self.out_proj = nn.Linear(hidden_size, hidden_size)
        self.dropout = nn.Dropout(dropout) if dropout > 0.0 else nn.Identity()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch_size, steps, hidden_size = x.shape
        qkv = self.qkv(x).view(batch_size, steps, 3, self.num_heads, self.head_dim)
        q, k, v = qkv.unbind(dim=2)
        q = q.permute(0, 2, 1, 3)
        k = k.permute(0, 2, 1, 3)
        v = v.permute(0, 2, 1, 3)
        scores = torch.matmul(q, k.transpose(-2, -1)) * self.scale
        mask = torch.triu(torch.ones(steps, steps, device=x.device, dtype=torch.bool), diagonal=1)
        scores = scores.masked_fill(mask, float("-inf"))
        attn = torch.softmax(scores, dim=-1)
        attn = self.dropout(attn)
        out = torch.matmul(attn, v)
        out = out.permute(0, 2, 1, 3).contiguous().view(batch_size, steps, hidden_size)
        return self.out_proj(out)


class _ConformerLiteBlock(nn.Module):
    def __init__(self, hidden_size: int, dropout: float, num_heads: int) -> None:
        super().__init__()
        self.ffn1 = nn.Sequential(
            nn.LayerNorm(hidden_size),
            nn.Linear(hidden_size, hidden_size * 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_size * 2, hidden_size),
        )
        self.attn_norm = nn.LayerNorm(hidden_size)
        self.attn = _CausalSelfAttention(hidden_size, num_heads=num_heads, dropout=dropout)
        self.conv_norm = nn.LayerNorm(hidden_size)
        self.depthwise = _CausalDepthwiseConv1d(hidden_size, kernel_size=3)
        self.pointwise = nn.Conv1d(hidden_size, hidden_size, kernel_size=1)
        self.dropout = nn.Dropout(dropout) if dropout > 0.0 else nn.Identity()
        self.ffn2 = nn.Sequential(
            nn.LayerNorm(hidden_size),
            nn.Linear(hidden_size, hidden_size * 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_size * 2, hidden_size),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x + 0.5 * self.ffn1(x)
        x = x + self.dropout(self.attn(self.attn_norm(x)))
        conv_input = self.conv_norm(x).transpose(1, 2)
        conv_out = self.pointwise(F.gelu(self.depthwise(conv_input))).transpose(1, 2)
        x = x + self.dropout(conv_out)
        x = x + 0.5 * self.ffn2(x)
        return x


class ConformerLiteRegressor(nn.Module):
    def __init__(
        self,
        n_channels: int,
        n_outputs: int,
        hidden_size: int = 128,
        num_layers: int = 2,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.input_proj = nn.Linear(n_channels, hidden_size)
        num_heads = 4 if hidden_size % 4 == 0 else 1
        self.blocks = nn.ModuleList(
            [_ConformerLiteBlock(hidden_size, dropout, num_heads=num_heads) for _ in range(max(1, int(num_layers)))]
        )
        self.output_norm = nn.LayerNorm(hidden_size)
        self.head = nn.Linear(hidden_size, n_outputs)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.input_proj(x.transpose(1, 2))
        for block in self.blocks:
            h = block(h)
        return self.head(self.output_norm(h[:, -1, :]))
