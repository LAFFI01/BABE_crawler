"""Configuration file loader"""

import yaml
import json
from pathlib import Path
from typing import Dict, Any, Optional


def load_config(config_path: str) -> Dict[str, Any]:
    """
    Load configuration from YAML or JSON file
    
    Args:
        config_path: Path to configuration file
        
    Returns:
        Configuration dictionary
        
    Raises:
        FileNotFoundError: If config file not found
        ValueError: If config file format is invalid
    """
    config_file = Path(config_path)
    
    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    try:
        if config_file.suffix.lower() == ".yaml" or config_file.suffix.lower() == ".yml":
            with open(config_file, "r") as f:
                config = yaml.safe_load(f)
        elif config_file.suffix.lower() == ".json":
            with open(config_file, "r") as f:
                config = json.load(f)
        else:
            raise ValueError(f"Unsupported config format: {config_file.suffix}")
        
        return config or {}
    
    except Exception as e:
        raise ValueError(f"Error loading config file: {str(e)}")


def merge_configs(*configs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge multiple configuration dictionaries
    
    Args:
        *configs: Variable number of config dictionaries
        
    Returns:
        Merged configuration dictionary
    """
    merged = {}
    for config in configs:
        if config:
            merged.update(config)
    return merged
