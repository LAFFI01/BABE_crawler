# BABE Crawler - Production Web Scraper

A production-level web scraper built with Python, featuring advanced capabilities for reliable and scalable data extraction.

## Features

- 🚀 **High Performance**: Async/concurrent scraping with configurable workers
- 🔄 **Retry Logic**: Automatic retries with exponential backoff
- 💾 **Multiple Storage Options**: PostgreSQL, MongoDB, CSV, JSON
- 🛡️ **Proxy & Headers Rotation**: User-agent and proxy rotation support
- 📊 **Data Validation**: Built-in data validation and cleaning
- 🔍 **Error Handling**: Comprehensive error handling and logging
- 📝 **Configurable**: YAML-based configuration management
- 🐳 **Containerized**: Dockerfile and docker-compose included
- 📈 **Monitoring**: Logging, metrics, and performance tracking
- ✅ **Unit Testing**: Comprehensive test suite

## Project Structure

```
BABE_crawler/
├── src/
│   ├── crawler/
│   │   ├── __init__.py
│   │   ├── spiders/          # Spider implementations
│   │   ├── middleware.py
│   │   └── engine.py
│   ├── storage/              # Data storage handlers
│   ├── utils/                # Utility functions
│   └── main.py
├── config/                   # Configuration files
├── tests/                    # Test suite
├── docker/                   # Dockerfile & compose
├── docs/                     # Documentation
├── logs/                     # Application logs
├── data/                     # Scraped data
├── requirements.txt
├── setup.py
└── README.md
```

## Installation

### Prerequisites
- Python 3.9+
- pip or conda

### Setup

1. Clone the repository:
```bash
git clone https://github.com/LAFFI01/BABE_crawler.git
cd BABE_crawler
```

2. Create virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure environment variables:
```bash
cp .env.example .env
# Edit .env with your configuration
```

## Quick Start

### Basic Usage
```python
from crawler.engine import CrawlerEngine

config = {
    'start_urls': ['https://example.com'],
    'max_workers': 5,
    'output_format': 'json'
}

engine = CrawlerEngine(config)
results = engine.run()
```

### Command Line
```bash
babe-crawler --config config/default.yaml
```

## Configuration

See `config/` directory for configuration examples:
- `default.yaml` - Default configuration
- `fast.yaml` - Performance-optimized settings
- `safe.yaml` - Respectful crawling settings

## API Reference

### CrawlerEngine
Main crawler orchestrator with support for multiple sources.

### StorageManager
Handles data persistence to various backends.

### ProxyRotator
Manages proxy and user-agent rotation.

## Testing

Run tests:
```bash
pytest tests/ -v
pytest tests/ --cov=src  # With coverage
```

## Docker

Build and run with Docker:
```bash
docker build -f docker/Dockerfile -t babe-crawler .
docker-compose -f docker/docker-compose.yml up
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a PR

## License

MIT License - see LICENSE file

## Support

For issues, questions, or suggestions, please open an issue on GitHub.

## Roadmap

- [ ] Async Scrapy integration
- [ ] Advanced caching mechanisms
- [ ] Real-time dashboard
- [ ] Distributed crawling
- [ ] Cloud deployment templates
