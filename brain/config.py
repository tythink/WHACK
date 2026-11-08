"""Configuration and environment loader for Role 3: Brain service."""

import os
from pathlib import Path
from typing import Optional, Dict

BASE_DIR = Path(__file__).resolve().parent.parent
BRAIN_DIR = (BASE_DIR / "brain").resolve()
CACHE_DIR = (BRAIN_DIR / "cache").resolve()
WIKI_CACHE_DIR = (CACHE_DIR / "wiki").resolve()

def load_env_file(env_path: Path):
    """Load key-value pairs from an .env file into os.environ if not already set or override."""
    if not env_path.exists():
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    if key:
                        os.environ[key] = val
    except Exception:
        pass

# Load brain/.env
load_env_file(BRAIN_DIR / ".env")

# Target demo repository outside this workspace (configurable, defaulting to ~/whack-demo-repo)
DEFAULT_GENERATED_DEMO_REPO = Path(os.path.expanduser("~/whack-demo-repo")).resolve()
DEMO_REPO_DIR = Path(
    os.environ.get("WHACK_DEMO_REPO")
    or os.environ.get("DEMO_REPO_DIR")
    or (DEFAULT_GENERATED_DEMO_REPO if DEFAULT_GENERATED_DEMO_REPO.exists() else (BASE_DIR / "demo-repo"))
).resolve()

# Service URLs and Ports
CORE_SERVER_URL = os.environ.get("CORE_SERVER_URL", "http://localhost:3000")
BRAIN_PORT = int(os.environ.get("BRAIN_PORT", os.environ.get("PORT", "5002")))
BRAIN_HOST = os.environ.get("BRAIN_HOST", "0.0.0.0")

# Diagnostic & LLM Settings
LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS", "8.0"))
MAX_TOOL_TURNS = int(os.environ.get("MAX_TOOL_TURNS", "6"))

# Provider and model choice
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").lower()
LLM_MODEL = os.environ.get("LLM_MODEL") or ("gemini-3.5-flash-lite" if LLM_PROVIDER in ("gemini", "google") else "gpt-4o-mini")

# API Keys
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

def sanitize_secret(text: str) -> str:
    """Strip any active API keys from error messages or logs."""
    for key in [GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY]:
        if key and len(key) > 6:
            text = text.replace(key, "[REDACTED_API_KEY]")
    return text

def get_active_api_key(provider: Optional[str] = None) -> Optional[str]:
    """Retrieve the API key for the requested or configured provider without printing it."""
    prov = (provider or LLM_PROVIDER).lower()
    if prov in ("gemini", "google"):
        return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    elif prov == "openai":
        return os.environ.get("OPENAI_API_KEY")
    elif prov == "anthropic":
        return os.environ.get("ANTHROPIC_API_KEY")
    return None

def require_api_key(provider: Optional[str] = None) -> str:
    """Validate that the required API key is present; fail with a clear message naming the variable without printing any secret."""
    prov = (provider or LLM_PROVIDER).lower()
    key = get_active_api_key(prov)
    if not key or not key.strip():
        if prov in ("gemini", "google"):
            var_name = "GEMINI_API_KEY (or GOOGLE_API_KEY)"
        elif prov == "openai":
            var_name = "OPENAI_API_KEY"
        elif prov == "anthropic":
            var_name = "ANTHROPIC_API_KEY"
        else:
            var_name = f"{prov.upper()}_API_KEY"
        raise ValueError(
            f"Missing required API key for provider '{prov}'. "
            f"Please populate '{var_name}' in brain/.env or environment variables."
        )
    return key.strip()
