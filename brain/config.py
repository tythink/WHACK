"""Configuration for Role 3: Brain service."""

import os
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DEMO_REPO_DIR = (BASE_DIR / "demo-repo").resolve()
BRAIN_DIR = (BASE_DIR / "brain").resolve()

# Service URLs and Ports
CORE_SERVER_URL = os.environ.get("CORE_SERVER_URL", "http://localhost:3000")
BRAIN_PORT = int(os.environ.get("BRAIN_PORT", os.environ.get("PORT", "5002")))
BRAIN_HOST = os.environ.get("BRAIN_HOST", "0.0.0.0")

# Diagnostic & LLM Settings
LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS", "8.0"))
MAX_TOOL_TURNS = int(os.environ.get("MAX_TOOL_TURNS", "6"))

# API Keys (Optional - fallback engine used if absent)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
