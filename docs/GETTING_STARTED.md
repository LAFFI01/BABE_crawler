# Getting Started with BABE Crawler

## Prerequisites

- Python 3.9 or higher
- pip or conda
- Git
- (Optional) Docker and Docker Compose

## Installation Steps

### 1. Clone the Repository

```bash
git clone https://github.com/LAFFI01/BABE_crawler.git
cd BABE_crawler
```

### 2. Create Virtual Environment

```bash
# Using venv
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Or using conda
conda create -n babe-crawler python=3.11
conda activate babe-crawler
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment

```bash
cp .env.example .env
# Edit .env with your settings
```

## Quick Start

### Run First Crawl

```bash
python -m crawler.main crawl --config config/default.yaml
```

### Using the CLI

```bash
# Check status
python -m crawler.main status

# Show version
python -m crawler.main version

# Run with custom config
python -m crawler.main crawl --config config/custom.yaml --output data/results.json
```

### Using Python API

```python
from src.crawler.engine import CrawlerEngine

config = {
    'start_urls': ['https://example.com'],
    'max_workers': 5,
    'output_format': 'json'
}

engine = CrawlerEngine(config)
results = engine.run()

print(engine.get_stats())
```

## Configuration

### Basic Configuration (config/default.yaml)

```yaml
crawler:
  name: "My Crawler"
  start_urls:
    - "https://example.com"
  max_workers: 5
  request_timeout: 10

storage:
  type: "json"
  path: "./data/"

logging:
  level: "INFO"
  file: "logs/crawler.log"
```

### Environment Variables

```bash
# Database
DB_TYPE=postgresql
DB_HOST=localhost
DB_PORT=5432
DB_NAME=crawler_db

# Crawler
MAX_WORKERS=5
REQUEST_TIMEOUT=10

# Output
OUTPUT_FORMAT=json
OUTPUT_PATH=./data/
```

## Common Tasks

### Custom Spider

Create `src/crawler/spiders/custom_spider.py`:

```python
from .base_spider import BaseSpider

class CustomSpider(BaseSpider):
    name = "custom_spider"
    allowed_domains = ["example.com"]
    start_urls = ["https://example.com"]
    
    def parse(self, response):
        items = []
        for item in response.find_all("div", class_="item"):
            items.append({
                "title": item.find("h2").text,
                "url": item.find("a")["href"],
            })
        return items
```

### Database Storage

Configure in `.env`:

```bash
DB_TYPE=postgresql
DB_HOST=localhost
DB_PORT=5432
DB_NAME=crawler_db
DB_USERNAME=crawler_user
DB_PASSWORD=password
```

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# With coverage
pytest tests/ -v --cov=src

# Specific test
pytest tests/test_engine.py -v
```

## Docker Deployment

### Build Image

```bash
docker build -f docker/Dockerfile -t babe-crawler:latest .
```

### Run with Docker Compose

```bash
docker-compose -f docker/docker-compose.yml up -d
```

### View Logs

```bash
docker-compose -f docker/docker-compose.yml logs -f crawler
```

## Troubleshooting

### Import Errors

```bash
# Ensure you're in the virtual environment
source venv/bin/activate

# Reinstall package in development mode
pip install -e .
```

### Database Connection Issues

```bash
# Check database configuration
cat .env | grep DB_

# Test connection
python -c "from src.storage import BaseStorage; print('OK')"
```

### Missing Dependencies

```bash
# Reinstall all requirements
pip install -r requirements.txt --force-reinstall
```

## Next Steps

1. Read [ARCHITECTURE.md](./ARCHITECTURE.md) for system design
2. Create custom spiders for your data sources
3. Configure storage backend
4. Set up monitoring and logging
5. Deploy to production

## Getting Help

- Check the [README](../README.md)
- Review example configurations in `config/`
- Check logs in `logs/`
- Open an issue on GitHub
