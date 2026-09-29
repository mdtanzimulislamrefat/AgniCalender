"""Backend data paths are relative to the package, not the shell directory."""
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
RAW_DIR = BACKEND_DIR / 'data' / 'raw'
OUTPUT_DIR = BACKEND_DIR / 'data' / 'output'
