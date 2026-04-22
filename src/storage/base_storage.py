"""Base storage class for data persistence"""

import logging
from typing import Dict, List, Any, Optional
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class BaseStorage(ABC):
    """Abstract base class for all storage handlers"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize storage handler
        
        Args:
            config: Optional configuration dictionary
        """
        self.config = config or {}
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def save(self, data: List[Dict[str, Any]]) -> bool:
        """
        Save data to storage
        
        Args:
            data: List of items to save
            
        Returns:
            True if successful, False otherwise
        """
        pass

    @abstractmethod
    def load(self, query: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Load data from storage
        
        Args:
            query: Optional query filter
            
        Returns:
            List of loaded items
        """
        pass

    @abstractmethod
    def delete(self, query: Dict[str, Any]) -> int:
        """
        Delete data from storage
        
        Args:
            query: Query filter for deletion
            
        Returns:
            Number of deleted items
        """
        pass

    def close(self) -> None:
        """Close storage connection"""
        pass
