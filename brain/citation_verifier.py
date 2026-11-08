"""Citation verifier for grounding checks against any target workspace and git diff."""

import re
from pathlib import Path
from typing import Tuple, List, Optional, Dict

from brain.config import resolve_repo_dir
from brain.models import DiagnosisResponse
from brain.tools import git_diff
from brain.extractor import normalize_repo_file_path


def parse_diff_changed_lines(diff_text: str) -> Dict[str, List[Tuple[int, int]]]:
    """Parse unified diff to extract changed line ranges [start, end] for each modified file."""
    changed_ranges: Dict[str, List[Tuple[int, int]]] = {}
    current_file = None

    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:].strip()
            changed_ranges.setdefault(current_file, [])
        elif line.startswith("@@ ") and current_file:
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


def _resolve_within_repo(rel_file: str, target_repo: Path) -> Optional[Path]:
    norm = normalize_repo_file_path(rel_file, repo_dir=target_repo)
    if not norm:
        return None
    cand = (target_repo / norm).resolve()
    try:
        cand.relative_to(target_repo)
    except ValueError:
        return None
    return cand


def verify_citations(
    diagnosis: DiagnosisResponse, repo_dir: Optional[Path] = None
) -> Tuple[DiagnosisResponse, List[str], bool]:
    """Verify that every cited file exists inside `repo_dir` and line ranges are within bounds.

    Checks overlap against BOTH uncommitted `git diff HEAD` and `HEAD~1..HEAD`.
    """
    target_repo = Path(repo_dir).expanduser().resolve() if repo_dir else resolve_repo_dir()
    flags: List[str] = []
    any_diff_overlap = False

    # Combine uncommitted working-tree diff and last commit diff
    working_diff = git_diff(base_ref="WORKING", repo_dir=target_repo).get("diff", "")
    commit_diff = git_diff("HEAD~1", "HEAD", repo_dir=target_repo).get("diff", "")

    changed_lines_map: Dict[str, List[Tuple[int, int]]] = {}
    for diff_txt in (working_diff, commit_diff):
        for df, ranges in parse_diff_changed_lines(diff_txt).items():
            changed_lines_map.setdefault(df, []).extend(ranges)

    def check_file_overlap(file_name: str, line_range: Tuple[int, int]) -> bool:
        norm_q = file_name.replace("\\", "/").lstrip("./")
        clean_name = Path(norm_q).name
        for df, ranges in changed_lines_map.items():
            df_norm = df.replace("\\", "/").lstrip("./")
            if df_norm == norm_q or df_norm.endswith(norm_q) or Path(df_norm).name == clean_name:
                for hr in ranges:
                    if does_range_overlap(line_range, hr):
                        return True
        return False

    # 1. Verify top-level file and line range
    if diagnosis.file:
        file_path = _resolve_within_repo(diagnosis.file, target_repo)
        if file_path is None or not file_path.exists() or not file_path.is_file():
            flags.append(f"Top-level file '{diagnosis.file}' does not exist in target workspace.")
            diagnosis.uncertain = True
        else:
            diagnosis.file = file_path.relative_to(target_repo).as_posix()
            if diagnosis.lines:
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        total_lines = sum(1 for _ in f)
                    start, end = diagnosis.lines
                    if start < 1 or end < start or end > max(1, total_lines):
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
        cit_path = _resolve_within_repo(citation.file, target_repo)
        if cit_path is None or not cit_path.exists() or not cit_path.is_file():
            citation.valid = False
            flags.append(f"Cited file '{citation.file}' does not exist in target workspace.")
            diagnosis.uncertain = True
            continue

        citation.file = cit_path.relative_to(target_repo).as_posix()
        try:
            with open(cit_path, "r", encoding="utf-8", errors="ignore") as f:
                total_lines = sum(1 for _ in f)
            start, end = citation.lines
            if start < 1 or end < start or end > max(1, total_lines):
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
