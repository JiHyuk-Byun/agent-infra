"""YAML configuration loader."""

import os
from pathlib import Path
from typing import Optional, Union

import yaml

from agent_infra.config.schema import Config


def load_config(path: Optional[Union[str, Path]] = None) -> Config:
    """Load configuration from YAML file.

    Args:
        path: Path to config file. If None, checks AGENT_INFRA_CONFIG env var,
              then falls back to ./config.yaml

    Returns:
        Parsed Config object

    Raises:
        FileNotFoundError: If config file doesn't exist
        yaml.YAMLError: If config file is invalid YAML
        pydantic.ValidationError: If config doesn't match schema
    """
    if path is None:
        path = os.environ.get("AGENT_INFRA_CONFIG", "config.yaml")

    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        data = yaml.safe_load(f)

    if data is None:
        data = {}

    return Config.model_validate(data)


def load_config_or_default(path: Optional[Union[str, Path]] = None) -> Config:
    """Load configuration, returning defaults if file doesn't exist.

    Args:
        path: Path to config file

    Returns:
        Parsed Config object or default Config
    """
    try:
        return load_config(path)
    except FileNotFoundError:
        return Config()
