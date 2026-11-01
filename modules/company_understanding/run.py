"""Standalone GUI: python run.py (port 8766 by default)."""
from company_understanding.cli import main

if __name__ == "__main__":
    main(["serve"])
