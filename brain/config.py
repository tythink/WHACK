"""Configuration and hot-reloading environment loader for Role 3: Brain service."""

import os
import re
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

BASE_DIR = Path(__file__).resolve().parent.parent
BRAIN_DIR = (BASE_DIR / "brain").resolve()
CACHE_DIR = (BRAIN_DIR / "cache").resolve()
WIKI_CACHE_DIR = (CACHE_DIR / "wiki").resolve()

# Tracked .env files in priority order (later files override earlier ones on reload)
ENV_CANDIDATES: Tuple[Path, ...] = (
    (BASE_DIR / ".env").resolve(),
    (BRAIN_DIR / ".env").resolve(),
)

_ENV_FILE_SIGNATURES: Dict[Path, Optional[Tuple[int, int]]] = {}
_ACCIDENTAL_KEY_PREFIX_RE = re.compile(
    r"^(?:export\s+)?(?:GEMINI_API_KEY|GOOGLE_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY)\s*=\s*",
    re.IGNORECASE,
)


def _file_signature(env_path: Path) -> Optional[Tuple[int, int]]:
    try:
        if not env_path.exists():
            return None
        st = env_path.stat()
        return (st.st_mtime_ns, st.st_size)
    except Exception:
        return None


def load_env_file(env_path: Path, override: bool = False) -> None:
    """Load key-value pairs from an .env file into os.environ (UTF-8 / UTF-8-BOM safe)."""
    if not env_path.exists():
        return
    try:
        with open(env_path, "r", encoding="utf-8-sig") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.lower().startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip("'\"")
                if key.endswith("_API_KEY") and val:
                    val = _ACCIDENTAL_KEY_PREFIX_RE.sub("", val).strip().strip("'\"")
                if not key:
                    continue
                if override:
                    if val:
                        os.environ[key] = val
                else:
                    if val and not os.environ.get(key):
                        os.environ[key] = val
                    elif key not in os.environ:
                        os.environ[key] = val
    except Exception:
        pass


def refresh_env_files(force: bool = False) -> bool:
    """Hot-reload root `.env` and `brain/.env` whenever created or modified on disk.

    Safe for unit tests using `monkeypatch.setenv` / `monkeypatch.delenv` because it only
    mutates `os.environ` after initial import when a `.env` file's mtime/size changes
    on disk (or when `force=True` is requested via `POST /reload-env`).
    """
    initial_load = len(_ENV_FILE_SIGNATURES) == 0
    changed = False

    for env_path in ENV_CANDIDATES:
        sig = _file_signature(env_path)
        prev_Known = env_path in _ENV_FILE_SIGNATURES
        prev_sig = _ENV_FILE_SIGNATURES.get(env_path)
        if force or not prev_Known or sig != prev_sig:
            _ENV_FILE_SIGNATURES[env_path] = sig
            if sig is not None:
                load_env_file(env_path, override=(force or not initial_load))
                changed = True

    return changed


# Initial load at import time
refresh_env_files(force=False)

# Target demo repository (configurable, defaulting to ~/whack-demo-repo if present, else workspace demo-repo)
DEFAULT_GENERATED_DEMO_REPO = Path(os.path.expanduser("~/whack-demo-repo")).resolve()
DEMO_REPO_DIR = Path(
    os.environ.get("WHACK_DEMO_REPO")
    or os.environ.get("DEMO_REPO_DIR")
    or (DEFAULT_GENERATED_DEMO_REPO if DEFAULT_GENERATED_DEMO_REPO.exists() else (BASE_DIR / "demo-repo"))
).resolve()


def resolve_repo_dir(repo_path: Optional[Union[str, Path]] = None) -> Path:
    """Resolve the active workspace directory, falling back to DEMO_REPO_DIR when unset or non-existent."""
    if repo_path is not None:
        raw = str(repo_path).strip()
        if raw:
            try:
                candidate = Path(os.path.expanduser(raw))
                if candidate.exists() and candidate.is_dir():
                    return candidate.resolve()
            except Exception:
                pass
    env_override = os.environ.get("WHACK_DEMO_REPO") or os.environ.get("DEMO_REPO_DIR")
    if env_override:
        try:
            env_candidate = Path(os.path.expanduser(env_override.strip()))
            if env_candidate.exists() and env_candidate.is_dir():
                return env_candidate.resolve()
        except Exception:
            pass
    return DEMO_REPO_DIR.resolve()


# Service URLs and Ports
CORE_SERVER_URL = os.environ.get("CORE_SERVER_URL", "http://localhost:3000")
BRAIN_PORT = int(os.environ.get("BRAIN_PORT", os.environ.get("PORT", "5002")))
BRAIN_HOST = os.environ.get("BRAIN_HOST", "0.0.0.0")

# Diagnostic & LLM Settings
LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS") or "45.0")
MAX_TOOL_TURNS = int(os.environ.get("MAX_TOOL_TURNS") or "4")

_DEPRECATED_GEMINI_MODELS = frozenset(
    {
        "gemini-2.5-flash",
        "models/gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "models/gemini-2.5-flash-lite",
        "gemini-3.5-flash-lite",
        "models/gemini-3.5-flash-lite",
        "gemini-1.5-flash",
        "models/gemini-1.5-flash",
    }
)


def get_active_provider(provider: Optional[str] = None) -> str:
    """Return the active normalized LLM provider name (auto-detecting key when LLM_PROVIDER is unset)."""
    refresh_env_files(force=False)
    explicit = (provider or os.environ.get("LLM_PROVIDER") or "").strip().lower()
    if explicit:
        return "gemini" if explicit == "google" else explicit

    # If LLM_PROVIDER was not set explicitly, infer from whichever key is actually populated
    if (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip():
        return "gemini"
    if (os.environ.get("OPENAI_API_KEY") or "").strip():
        return "openai"
    if (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        return "anthropic"
    return "gemini"


def get_default_model(provider: Optional[str] = None) -> str:
    """Return the provider-appropriate default real model identifier."""
    prov = get_active_provider(provider)
    if prov in ("gemini", "google"):
        return "gemini-3.1-flash-lite-preview"
    if prov == "openai":
        return "gpt-4o-mini"
    if prov == "anthropic":
        return "claude-3-5-haiku-latest"
    return "gemini-3.1-flash-lite-preview"


def get_active_model(provider: Optional[str] = None) -> str:
    """Return configured LLM_MODEL if set (auto-upgrading retired Gemini 2.5 IDs), else default."""
    refresh_env_files(force=False)
    env_model = (os.environ.get("LLM_MODEL") or "").strip()
    if env_model:
        if env_model.lower() in _DEPRECATED_GEMINI_MODELS:
            return "gemini-3.1-flash-lite-preview"
        return env_model
    return get_default_model(provider)


# Provider and model choice
LLM_PROVIDER = get_active_provider()
LLM_MODEL = get_active_model(LLM_PROVIDER)

# API Keys (import-time snapshots; use get_active_api_key() for dynamic lookups)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")


def sanitize_secret(text: str) -> str:
    """Strip any active API keys from error messages or logs."""
    candidates = [
        GEMINI_API_KEY,
        OPENAI_API_KEY,
        ANTHROPIC_API_KEY,
        os.environ.get("GEMINI_API_KEY"),
        os.environ.get("GOOGLE_API_KEY"),
        os.environ.get("OPENAI_API_KEY"),
        os.environ.get("ANTHROPIC_API_KEY"),
    ]
    for key in candidates:
        if key and len(key) > 6:
            text = text.replace(key, "[REDACTED_API_KEY]")
    return text


def get_active_api_key(provider: Optional[str] = None) -> Optional[str]:
    """Retrieve the API key for the requested or configured provider without printing it."""
    refresh_env_files(force=False)
    prov = get_active_provider(provider)
    if prov in ("gemini", "google"):
        val = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    elif prov == "openai":
        val = os.environ.get("OPENAI_API_KEY")
    elif prov == "anthropic":
        val = os.environ.get("ANTHROPIC_API_KEY")
    else:
        val = None
    if not val:
        return None
    cleaned = val.strip().strip("'\"")
    if not cleaned or cleaned.startswith("your_") or cleaned.startswith("paste_"):
        return None
    return cleaned


def expected_api_key_var(provider: Optional[str] = None) -> str:
    """Return human-readable environment variable name expected for provider."""
    prov = get_active_provider(provider)
    if prov in ("gemini", "google"):
        return "GEMINI_API_KEY (or GOOGLE_API_KEY)"
    if prov == "openai":
        return "OPENAI_API_KEY"
    if prov == "anthropic":
        return "ANTHROPIC_API_KEY"
    return f"{prov.upper()}_API_KEY"


def require_api_key(provider: Optional[str] = None) -> str:
    """Validate that the required API key is present; fail clearly without printing secrets."""
    prov = get_active_provider(provider)
    key = get_active_api_key(prov)
    if not key:
        var_name = expected_api_key_var(prov)
        raise ValueError(
            f"Missing required API key for provider '{prov}'. "
            f"Please populate '{var_name}' in brain/.env or environment variables."
        )
    return key
