"""Fail-closed backend gates against an isolated, disposable PostgreSQL database."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def run(arguments: list[str], *, env: dict[str, str] | None = None) -> bool:
    print(f"GATE {datetime.now(UTC).isoformat()} {' '.join(arguments)}", flush=True)
    result = subprocess.run(arguments, check=False, env=env)
    print(f"RESULT exit={result.returncode}", flush=True)
    return result.returncode == 0


def main() -> int:
    admin_url = os.environ["TEST_ADMIN_DATABASE_URL"]
    if not admin_url.rsplit("/", 1)[-1].startswith("closure_test"):
        raise RuntimeError("Quality gate refuses non-test databases")
    migration_env = {**os.environ, "DATABASE_URL": admin_url}
    if not run(["alembic", "upgrade", "head"], env=migration_env):
        return 1
    # Delivered revisions 01-03 and vendored historical snapshots are immutable.
    # Their byte hashes are validated by convergence tests, not rewritten by Ruff.
    lint_paths = [
        "app",
        "tests",
        "alembic/env.py",
        "alembic/versions/20260920_04_closure_security_convergence.py",
        "alembic/versions/20260924_05_lab_assets.py",
        "quality_gate.py",
    ]
    results = [
        run(["ruff", "format", "--check", *lint_paths]),
        run(["ruff", "check", *lint_paths]),
        run(["pytest", "-q", "-p", "no:cacheprovider", "--cov-report=json:/tmp/coverage.json"]),
    ]
    coverage_path = Path("/tmp/coverage.json")
    if coverage_path.exists():
        totals = json.loads(coverage_path.read_text())["totals"]
        branch = 100 * totals["covered_branches"] / max(totals["num_branches"], 1)
        lines = 100 * totals["covered_lines"] / max(totals["num_statements"], 1)
        print(f"COVERAGE lines={lines:.2f}% branches={branch:.2f}%", flush=True)
        results.append(branch >= 90)
    else:
        results.append(False)
    results.append(run(["pip-audit", "--version"]))
    results.append(run(["pip-audit", "--progress-spinner=off", "-r", "requirements.txt"]))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
