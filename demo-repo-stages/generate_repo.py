#!/usr/bin/env python3
"""Convenience runner for tools/setup_demo_repo.py"""

import sys
from pathlib import Path

# Add repo root to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.setup_demo_repo import main

if __name__ == "__main__":
    main()
