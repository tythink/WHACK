"""Citation verifier for grounding checks against the target repository and git diff."""

import re
from pathlib import Path
from typing import Tuple, List, Optional, Dict
from brain.config import DEMO_REPO_DIR
from brain.models import DiagnosisResponse, CodeCitation
from brain.tools import git_diff

def parse_diff_changed_lines(diff_text: str) -> Dict[str, List[Tuple[int, int]]]:
    """Parse unified diff to extract changed line ranges [start, end] for each modified file."""
    changed_ranges: Dict[str, List[Tuple[int, int]]] = {}
    current_file = None

    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:].strip()
            changed_ranges.setdefault(current_file, [])
        elif line.startswith("@@ ") and current_file:
            # e.g. @@ -13,8 +13,9 @@
            match = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if match:
                start = int(match.group(1))
                count = int(match.group(2)) if match.group(2) else 1
                end = start + count - 1 if count > 0 else start
                changed_ranges[current_file].append((start, end))

    return changed_ranges

def does_range_overlap(r1: Tuple[int, int], r2: Tuple[int, int]) -> bool:
    """Check if two inclusive line ranges overlap."""
    return max(r1[0], r2[0]) <= min(r1[1], r2[1])

def verify_citations(
    diagnosis: DiagnosisResponse, repo_dir: Optional[Path] = None
) -> Tuple[DiagnosisResponse, List[str], bool]:
    """Verify in code that every cited file exists and every cited line range is within bounds.
    
    Also checks whether cited ranges overlap with lines changed in the diff between
    the last passing commit and HEAD.
    
    Returns:
    (updated_diagnosis, flags, any_diff_overlap)
    """
    target_repo = Path(repo_dir or DEMO_REPO_DIR).resolve()
    flags = []
    any_diff_overlap = False

    # Get diff changed line ranges
    diff_data = git_diff("HEAD~1", "HEAD")
    changed_lines_map = parse_diff_changed_lines(diff_data.get("diff", ""))

    def check_file_overlap(file_name: str, line_range: Tuple[int, int]) -> bool:
        clean_name = Path(file_name).name
        for df, ranges in changed_lines_map.items():
            if Path(df).name == clean_name:
                for hr in ranges:
                    if does_range_overlap(line_range, hr):
                        return True
        return False

    # 1. Verify top-level file and line range
    if diagnosis.file:
        file_path = (target_repo / diagnosis.file.lstrip("/\\")).resolve()
        if not file_path.exists() or not file_path.is_file():
            flags.append(f"Top-level file '{diagnosis.file}' does not exist in target repository.")
            diagnosis.uncertain = True
        elif diagnosis.lines:
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    total_lines = sum(1 for _ in f)
                start, end = diagnosis.lines
                if start < 1 or end < start or end > total_lines:
                    flags.append(
                        f"Top-level line range [{start}, {end}] is out of bounds for '{diagnosis.file}' (total lines: {total_lines})."
                    )
                    diagnosis.uncertain = True
                else:
                    if check_file_overlap(diagnosis.file, diagnosis.lines):
                        any_diff_overlap = True
            except Exception as e:
                flags.append(f"Failed to read '{diagnosis.file}': {str(e)}")
                diagnosis.uncertain = True

    # 2. Verify all items in citations list
    for citation in diagnosis.citations:
        cit_path = (target_repo / citation.file.lstrip("/\\")).resolve()
        if not cit_path.exists() or not cit_path.is_file():
            citation.valid = False
            flags.append(f"Cited file '{citation.file}' does not exist.")
            diagnosis.uncertain = True
            continue

        try:
            with open(cit_path, "r", encoding="utf-8", errors="ignore") as f:
                total_lines = sum(1 for _ in f)
            start, end = citation.lines
            if start < 1 or end < start or end > total_lines:
                citation.valid = False
                flags.append(
                    f"Cited line range [{start}, {end}] exceeds line count {total_lines} in '{citation.file}'."
                )
                diagnosis.uncertain = True
            else:
                citation.valid = True
                overlaps = check_file_overlap(citation.file, citation.lines)
                citation.diff_overlap = overlaps
                if overlaps:
                    any_diff_overlap = True
        except Exception as e:
            citation.valid = False
            flags.append(f"Error checking citation '{citation.file}': {str(e)}")
            diagnosis.uncertain = True

    return diagnosis, flags, any_diff_overlap
