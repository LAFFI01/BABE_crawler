"""Base spider class for all crawler implementations"""

import logging
from typing import Dict, List, Any, Optional
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class BaseSpider(ABC):
    """Abstract base class for all spiders"""

    name: str = "base_spider"
    allowed_domains: List[str] = []
    start_urls: List[str] = []

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the spider
        
        Args:
            config: Optional configuration dictionary
        """
        self.config = config or {}
        self.logger = logging.getLogger(self.name)
        self.logger.info(f"Initializing {self.name}")

    @abstractmethod
    def parse(self, response: Any) -> List[Dict[str, Any]]:
        """
        Parse response and extract data
        
        Args:
            response: Response object from HTTP request
            
        Returns:
            List of extracted items
        """
        pass

    def process_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process and validate extracted item
        
        Args:
            item: Extracted item dictionary
            
        Returns:
            Processed item
        """
        # TODO: Add validation and processing logic
        return item

    def __repr__(self) -> str:
        return f"<{self.name} Spider>"
