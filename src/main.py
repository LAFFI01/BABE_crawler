"""Main entry point for the crawler"""

import logging
import sys
from pathlib import Path
from typing import Optional

import click

from crawler.engine import CrawlerEngine
from utils.logger_config import setup_logger
from utils.config_loader import load_config


# Setup logger
logger = setup_logger("babe_crawler", log_file="logs/crawler.log")


@click.group()
def cli():
    """BABE Crawler - Production Web Scraper"""
    pass


@cli.command()
@click.option(
    "--config",
    type=click.Path(exists=True),
    default="config/default.yaml",
    help="Configuration file path"
)
@click.option(
    "--output",
    type=click.Path(),
    default="data/output.json",
    help="Output file path"
)
def crawl(config: str, output: str):
    """Run the crawler"""
    try:
        logger.info(f"Loading configuration from {config}")
        config_data = load_config(config)
        
        logger.info("Initializing crawler engine")
        engine = CrawlerEngine(config_data)
        
        logger.info("Starting crawl operation")
        results = engine.run()
        
        # Save results
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        import json
        with open(output_path, "w") as f:
            json.dump(results, f, indent=2)
        
        logger.info(f"Results saved to {output_path}")
        
        # Print statistics
        stats = engine.get_stats()
        click.echo("\n" + "="*50)
        click.echo("Crawling Statistics:")
        click.echo("="*50)
        for key, value in stats.items():
            click.echo(f"{key}: {value}")
        
    except Exception as e:
        logger.error(f"Error: {str(e)}", exc_info=True)
        click.echo(f"Error: {str(e)}", err=True)
        sys.exit(1)


@cli.command()
@click.option("--verbose", is_flag=True, help="Verbose output")
def status(verbose: bool):
    """Check crawler status"""
    click.echo("BABE Crawler Status: OK")
    click.echo(f"Version: 1.0.0")
    if verbose:
        click.echo(f"Config path: config/")
        click.echo(f"Data path: data/")
        click.echo(f"Log path: logs/")


@cli.command()
def version():
    """Show version"""
    click.echo("BABE Crawler v1.0.0")


if __name__ == "__main__":
    cli()
