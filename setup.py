from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="babe-crawler",
    version="1.0.0",
    author="Your Name",
    author_email="your.email@example.com",
    description="Production-level web scraper with advanced features",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/LAFFI01/BABE_crawler",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.9",
    entry_points={
        "console_scripts": [
            "babe-crawler=crawler.main:cli",
        ],
    },
    install_requires=[
        line.strip()
        for line in open("requirements.txt")
        if line.strip() and not line.startswith("#")
    ],
)
