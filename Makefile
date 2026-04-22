SHELL := /bin/bash
.PHONY: help setup install run run-pro clean

help:
	@echo "🕸️  Universal Web Scraper - Makefile Commands"
	@echo ""
	@echo "Setup & Installation:"
	@echo "  make setup       Create venv & install all dependencies (one-time)"
	@echo "  make install     Update dependencies only"
	@echo ""
	@echo "Running:"
	@echo "  make run         Start Standard version (fast, simple)"
	@echo "  make run-pro     Start PRO version (scheduling, caching, API, etc)"
	@echo ""
	@echo "Maintenance:"
	@echo "  make clean       Remove cache & temp files"
	@echo ""

setup:
	@echo "📦 Creating virtual environment..."
	python3 -m venv venv
	@echo "✓ Installing dependencies..."
	bash -c "source venv/bin/activate && pip install --upgrade pip setuptools wheel"
	bash -c "source venv/bin/activate && pip install -r requirements.txt"
	@echo "✅ Setup complete!"
	@echo "   Run: make run       (Standard version)"
	@echo "   Run: make run-pro   (PRO version with all features)"

install:
	@echo "📦 Updating dependencies..."
	bash -c "source venv/bin/activate && pip install --upgrade -r requirements.txt"
	@echo "✅ Dependencies updated"

run:
	@echo "🕷️  Starting Universal Web Scraper (Standard)..."
	@echo "📱 Open browser: http://localhost:5000"
	bash -c "source venv/bin/activate && python3 universal_scraper.py"

run-pro:
	@echo "🕷️  Starting Universal Web Scraper PRO..."
	@echo "✨ Features: Scheduling, Caching, Filtering, API, Database, Selector Testing"
	@echo "📱 Open browser: http://localhost:5000"
	bash -c "source venv/bin/activate && python3 universal_scraper_pro.py"

clean:
	@echo "🧹 Cleaning up..."
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache 2>/dev/null || true
	@echo "✅ Cleanup complete"

.DEFAULT_GOAL := help
