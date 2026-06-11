from __future__ import annotations

import torch
from torch import nn


class _StateSpaceLiteLayer(nn.Module):
    def __init__(self, input_dim: int, hidden_size: int, dropout: float) -> None:
        super().__init__()
        self.in_proj = nn.Linear(input_dim, hidden_size)
        self.state_proj = nn.Linear(hidden_size, hidden_size)
        self.decay = nn.Parameter(torch.zeros(hidden_size))
        self.dropout = nn.Dropout(dropout) if dropout > 0.0 else nn.Identity()
        self.norm = nn.LayerNorm(hidden_size)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch_size, steps, _ = x.shape
        state = x.new_zeros((batch_size, self.in_proj.out_features))
        outputs: list[torch.Tensor] = []
        decay = torch.sigmoid(self.decay).unsqueeze(0)
        for index in range(steps):
            driven = torch.tanh(self.in_proj(x[:, index, :]) + self.state_proj(state))
            state = decay * state + (1.0 - decay) * driven
            outputs.append(self.norm(self.dropout(state)))
        return torch.stack(outputs, dim=1)


class StateSpaceLiteRegressor(nn.Module):
    def __init__(
        self,
        n_channels: int,
        n_outputs: int,
        hidden_size: int = 128,
        num_layers: int = 2,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        layers: list[nn.Module] = []
        input_dim = n_channels
        for _ in range(max(1, int(num_layers))):
            layers.append(_StateSpaceLiteLayer(input_dim, hidden_size, dropout))
            input_dim = hidden_size
        self.layers = nn.ModuleList(layers)
        self.head = nn.Linear(hidden_size, n_outputs)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = x.transpose(1, 2)
        for layer in self.layers:
            h = layer(h)
        return self.head(h[:, -1, :])
