"""Fail when the counts quoted in the README drift from what pytest and dbt actually run.

    python .github/scripts/check_readme_counts.py pytest <package>     # run from anywhere
    python .github/scripts/check_readme_counts.py dbt <dbt build log>

`pytest` collects the package's tests with the current interpreter and compares the number with the
README sentence "N tests in `package`" / "N in `package`". `dbt` reads TOTAL=N from a `dbt build` log
and compares it with every "N nodes" in the README. `--readme PATH` checks another copy of the README.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ("ingestion", "loader", "ml", "dashboard")


def readme_test_counts(text: str, package: str) -> list[int]:
    return [int(n) for n in re.findall(rf"(\d+)\s+(?:tests\s+)?in\s+`{package}`", text)]


def readme_node_counts(text: str) -> list[int]:
    return [int(n) for n in re.findall(r"(\d+)\s+nodes\b", text)]


def collected_tests(package: str) -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT / package,
        capture_output=True,
        text=True,
        check=False,
    )
    found = re.search(r"^(\d+) tests? collected", result.stdout, re.MULTILINE)
    if result.returncode != 0 or not found:
        sys.exit(f"pytest could not collect the {package} tests:\n{result.stdout}{result.stderr}")
    return int(found.group(1))


def dbt_total(log: Path) -> int:
    found = re.findall(r"\bTOTAL=(\d+)", log.read_text(encoding="utf-8", errors="replace"))
    if not found:
        sys.exit(f"no 'Done. ... TOTAL=N' line in {log}: did dbt build finish?")
    return int(found[-1])


def compare(quoted: list[int], actual: int, what: str, source: str) -> list[str]:
    if not quoted:
        return [f"README gives no count of {what}"]
    return [f"README says {n} {what}, {source} {actual}" for n in quoted if n != actual]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--readme", type=Path, default=ROOT / "README.md")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pytest").add_argument("package", choices=PACKAGES)
    sub.add_parser("dbt").add_argument("log", type=Path)
    args = parser.parse_args()
    text = args.readme.read_text(encoding="utf-8")

    if args.command == "pytest":
        actual = collected_tests(args.package)
        errors = compare(
            readme_test_counts(text, args.package), actual, f"tests for {args.package}", "pytest collected"
        )
        ok = f"README test count for {args.package} matches pytest: {actual}"
    else:
        actual = dbt_total(args.log)
        errors = compare(readme_node_counts(text), actual, "dbt nodes", "dbt build ran")
        ok = f"README node count matches dbt build: {actual}"

    if errors:
        print("\n".join(errors))
        return 1
    print(ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
