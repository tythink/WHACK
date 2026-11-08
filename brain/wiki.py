"""Architecture Wiki Generator and Cache for target repository intent.

Generates and caches an architecture overview from the LAST PASSING commit
read strictly through git (git show), never modifying the target repository.
"""

import datetime
import os
import re
import subprocess
from pathlib import Path
from typing import Optional, Dict

from brain.config import DEMO_REPO_DIR, WIKI_CACHE_DIR, GEMINI_API_KEY, OPENAI_API_KEY, LLM_PROVIDER, LLM_MODEL
from brain.codemap import CodeMap

class ArchitectureWiki:
    def __init__(self, repo_dir: Optional[Path] = None):
        self.repo_dir = Path(repo_dir or DEMO_REPO_DIR).resolve()
        WIKI_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def get_last_passing_commit(self) -> str:
        """Find the last passing commit in the target repository.
        
        In the standard 3-commit demo structure, HEAD~1 is the last passing commit.
        Falls back to HEAD if HEAD~1 does not exist.
        """
        if not (self.repo_dir / ".git").exists():
            return "unknown"
        try:
            # Check HEAD~1 first
            res = subprocess.run(
                ["git", "rev-parse", "HEAD~1"],
                cwd=str(self.repo_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
            # If no HEAD~1, use HEAD
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(self.repo_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3,
            )
            if res.returncode == 0:
                return res.stdout.strip()
        except Exception:
            pass
        return "unknown"

    def read_git_file(self, commit: str, rel_path: str) -> Optional[str]:
        """Read a file at a specific commit using git show (read-only)."""
        try:
            res = subprocess.run(
                ["git", "show", f"{commit}:{rel_path}"],
                cwd=str(self.repo_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3,
            )
            if res.returncode == 0:
                return res.stdout
        except Exception:
            pass
        return None

    def get_cache_path(self, commit: str) -> Path:
        safe_name = re.sub(r"[^\w\-]", "_", str(self.repo_dir))
        return WIKI_CACHE_DIR / f"{safe_name}_{commit[:12]}.md"

    def generate_skeleton(self, commit: str) -> str:
        """Generate deterministic architecture skeleton from last passing commit."""
        readme = self.read_git_file(commit, "README.md") or ""
        pipeline_code = self.read_git_file(commit, "pipeline.py") or ""
        utils_code = self.read_git_file(commit, "utils.py") or ""

        # Extract architecture section from README if present
        arch_section = ""
        if "## Architecture" in readme:
            parts = readme.split("## Architecture", 1)[1].split("##", 1)[0].strip()
            arch_section = f"### System Overview\n{parts}\n\n"

        skeleton = [
            f"# Target Repository Architecture (Commit: {commit[:8]})",
            "",
            arch_section.strip() if arch_section else "### System Overview\nStreaming telemetry and metrics pipeline.",
            "",
            "### Module: pipeline",
            "- **Role**: Central ingestion and processing orchestrator (`MetricsPipeline`).",
            "- **Data Flow**: Receives raw telemetry records, extracts numerical metrics, triggers window offset slicing, and maps percentile aggregations.",
            "- **Design Decisions**: Kept stateless across batches; window buffer calculations are delegated to pure numerical utilities.",
            "",
            "### Module: utils",
            "- **Role**: Mathematical and array indexing utilities (`calculate_window_offsets`, `batch_slice`, `compute_percentiles`).",
            "- **Data Flow**: Slices arrays into index bounds `[start, end]` and computes statistical percentiles (p50, p90, p99).",
            "- **Design Decisions**: Decouples sliding window boundary arithmetic from memory allocation to prevent redundant buffer duplication.",
            "",
            "### Module: scenarios",
            "- **Role**: Edge-case and boundary routines.",
            "- **Design Decisions**: Standardized test harnesses for threshold validations.",
        ]
        return "\n".join(skeleton).strip()

    def get_or_build_wiki(self, allow_model: bool = True) -> str:
        """Retrieve cached wiki or generate fresh from last passing commit."""
        commit = self.get_last_passing_commit()
        cache_file = self.get_cache_path(commit)

        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                pass

        # Build fresh
        skeleton = self.generate_skeleton(commit)
        prose = skeleton

        # Optional model enhancement if API key is present and allowed
        if allow_model:
            api_key = GEMINI_API_KEY or OPENAI_API_KEY
            if api_key and LLM_PROVIDER in ("gemini", "openai"):
                try:
                    prose = self._synthesize_prose_with_model(skeleton)
                except Exception:
                    prose = skeleton

        # Add metadata header
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        full_wiki = (
            f"<!-- Antigravity Architecture Wiki -->\n"
            f"<!-- Commit: {commit} | Generated: {now_iso} -->\n\n"
            f"{prose}"
        )

        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write(full_wiki)
        except Exception:
            pass

        return full_wiki

    def _synthesize_prose_with_model(self, skeleton: str) -> str:
        """Call LLM once to polish the architecture wiki into a concise ~350 word guide."""
        prompt = (
            "You are a principal software architect. Below is a code skeleton for a codebase. "
            "Write a concise architecture document of at most 350 words total. "
            "Explain: (1) module roles, (2) data flow between them, and (3) design decisions explaining "
            "why the code is structured this way. "
            "Format with clean markdown headers: '### Module: <name>'. "
            "Do NOT mention any bugs or test failures.\n\n"
            f"{skeleton}"
        )
        if GEMINI_API_KEY and LLM_PROVIDER in ("gemini", "google"):
            from google import genai
            client = genai.Client(api_key=GEMINI_API_KEY)
            resp = client.models.generate_content(
                model=LLM_MODEL or "gemini-3.5-flash-lite",
                contents=[prompt],
            )
            if resp.text:
                return resp.text.strip()
        elif OPENAI_API_KEY and LLM_PROVIDER == "openai":
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
            )
            if resp.choices[0].message.content:
                return resp.choices[0].message.content.strip()

        return skeleton

    def get_section(self, module: str = "") -> str:
        """Return the wiki section relevant to module, or the whole overview if module is empty."""
        full_wiki = self.get_or_build_wiki()
        if not module or not module.strip():
            return full_wiki

        clean_mod = module.lower().replace(".py", "").strip()
        # Parse sections by ### Module: or ##
        sections = re.split(r"(?=###\s+Module:\s+)", full_wiki)
        matched = []
        for s in sections:
            header_match = re.search(r"###\s+Module:\s+(\w+)", s, re.IGNORECASE)
            if header_match and header_match.group(1).lower() == clean_mod:
                matched.append(s.strip())

        if matched:
            return "\n\n".join(matched)

        # Fallback to general overview snippet
        overview = full_wiki.split("### Module:")[0].strip()
        return overview if overview else full_wiki[:500]

# Global singleton
_global_wiki: Optional[ArchitectureWiki] = None

def get_wiki_manager(repo_dir: Optional[Path] = None) -> ArchitectureWiki:
    global _global_wiki
    target = Path(repo_dir or DEMO_REPO_DIR).resolve()
    if _global_wiki is None or _global_wiki.repo_dir != target:
        _global_wiki = ArchitectureWiki(target)
    return _global_wiki

def get_wiki(module: str = "") -> str:
    """Read-only tool returning the wiki section for a specific module."""
    return get_wiki_manager().get_section(module)
