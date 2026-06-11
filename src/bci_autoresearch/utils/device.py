from __future__ import annotations

import torch


def get_device() -> torch.device:
    """Return the best available device.

    Preference order on this starter:
    1. Apple Silicon GPU via MPS
    2. CPU
    """
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")
