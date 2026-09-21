#!/usr/bin/env python3
"""Fail on a small set of high-confidence secret patterns.

This lightweight gate is intentionally dependency-free. It complements, but does
not replace, GitHub secret scanning or a dedicated scanner in later phases.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SKIP_DIRECTORIES = {".git", ".venv", "node_modules", "dist", "coverage", "__pycache__"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".pdf", ".lock"}
SENSITIVE_FILENAMES = re.compile(r"(?:recovery[-_ ]?codes?|credentials?|secrets?)", re.I)
PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
}


def iter_files(root: Path):  # type: ignore[no-untyped-def]
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRECTORIES for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        yield path


def scan(root: Path) -> list[tuple[Path, str]]:
    findings: list[tuple[Path, str]] = []
    for path in iter_files(root):
        relative = path.relative_to(root)
        if SENSITIVE_FILENAMES.search(path.name) and path.name != "secret_scan.py":
            findings.append((relative, "sensitive filename"))
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(content):
                findings.append((relative, label))
    return findings


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    findings = scan(root)
    if findings:
        print("Potential secrets detected:")
        for path, label in findings:
            print(f"- {path}: {label}")
        return 1
    print("Secret scan passed: no high-confidence patterns detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
