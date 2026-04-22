"""Tests for the crawler engine"""

import pytest
from src.crawler.engine import CrawlerEngine


class TestCrawlerEngine:
    """Test cases for CrawlerEngine"""

    @pytest.fixture
    def sample_config(self):
        """Fixture providing sample configuration"""
        return {
            "name": "test_crawler",
            "start_urls": ["https://example.com"],
            "max_workers": 2,
        }

    def test_engine_initialization(self, sample_config):
        """Test engine initialization"""
        engine = CrawlerEngine(sample_config)
        assert engine.config == sample_config
        assert engine.results == []

    def test_get_stats_before_run(self, sample_config):
        """Test get_stats before running crawler"""
        engine = CrawlerEngine(sample_config)
        stats = engine.get_stats()
        
        assert stats["items_scraped"] == 0
        assert stats["start_time"] is None
        assert stats["end_time"] is None

    @pytest.mark.asyncio
    async def test_async_run(self, sample_config):
        """Test async crawler run"""
        engine = CrawlerEngine(sample_config)
        results = await engine.run_async()
        
        assert isinstance(results, list)
        assert engine.start_time is not None
        assert engine.end_time is not None
