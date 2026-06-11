from __future__ import annotations

import torch
from torch import nn


class MaskedAttentionPool(nn.Module):
    def __init__(self, hidden_size: int, *, dropout: float = 0.0) -> None:
        super().__init__()
        self.score = nn.Linear(hidden_size, 1)
        self.dropout = nn.Dropout(dropout) if dropout > 0.0 else nn.Identity()

    def forward(
        self,
        sequence: torch.Tensor,
        *,
        attention_mask: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        if sequence.ndim != 3:
            raise ValueError(f"Expected sequence with shape (B, T, H), got {tuple(sequence.shape)}")
        logits = self.score(sequence).squeeze(-1)
        if attention_mask is not None:
            mask = attention_mask.to(dtype=torch.bool, device=sequence.device)
            if mask.shape != logits.shape:
                raise ValueError(
                    f"attention_mask must have shape {tuple(logits.shape)}, got {tuple(mask.shape)}"
                )
            valid_rows = torch.any(mask, dim=1, keepdim=True)
            fallback_mask = torch.ones_like(mask, dtype=torch.bool, device=sequence.device)
            effective_mask = torch.where(valid_rows, mask, fallback_mask)
            logits = logits.masked_fill(~effective_mask, torch.finfo(logits.dtype).min)
        weights = torch.softmax(logits, dim=1)
        pooled = torch.sum(sequence * weights.unsqueeze(-1), dim=1)
        return self.dropout(pooled), weights
