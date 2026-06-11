from __future__ import annotations

from .config import get_provider_config_path
from .config import get_provider_secrets_path
from .config import load_provider_config
from .config import load_provider_secrets
from .config import resolve_agent_provider_model
from .config import set_agent_provider_model
from .config import set_default_provider
from .config import write_provider_config
from .config import write_provider_secret
from .service import generate_json_task, list_provider_statuses, set_agent_model, test_provider

provider_list = list_provider_statuses
provider_set = set_default_provider
provider_test = test_provider

__all__ = [
    "generate_json_task",
    "get_provider_config_path",
    "get_provider_secrets_path",
    "list_provider_statuses",
    "load_provider_config",
    "load_provider_secrets",
    "provider_list",
    "provider_set",
    "provider_test",
    "resolve_agent_provider_model",
    "set_agent_model",
    "set_agent_provider_model",
    "set_default_provider",
    "test_provider",
    "write_provider_config",
    "write_provider_secret",
]
