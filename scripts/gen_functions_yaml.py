"""
Regenerate functions.yaml from the decorators in main.py.

Why this exists
---------------
The Firebase CLI normally discovers a Python codebase by importing main.py in
the project venv and reading the manifest back. That import pulls in app.py and
the whole ML stack (pandas, catboost, lightgbm, ...), which the venv does not
have, so a pre-generated functions.yaml is used instead — the CLI logs
"Found functions.yaml" and skips discovery entirely.

That shortcut is fine, but the file that was there had been written by hand and
only declared entryPoint, httpsTrigger and availableMemoryMb. Because it
overrides the decorators, every other option in main.py was silently discarded
on deploy:

  - timeout_sec=300 never applied, so the function kept the 60s default and
    every agent request died at the platform timeout with a 504.
  - secrets=[ANTHROPIC_API_KEY] never applied, so no secret was bound and
    ANTHROPIC_API_KEY was absent from the runtime environment. The key only
    appeared to work because .env was injecting it as a plaintext env var.

So functions.yaml is now a build artifact, not a source file: this script emits
exactly what the CLI's own discovery step would, keeping the decorators in
main.py as the single source of truth.

Run from the repo root with an interpreter that can import main (the system
Python, not venv/), and re-run after changing any option in main.py:

    python scripts/gen_functions_yaml.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from firebase_functions.private import serving  # noqa: E402

import main  # noqa: E402

OUT = ROOT / "functions.yaml"

BANNER = (
    "# GENERATED FILE - DO NOT EDIT BY HAND.\n"
    "# Regenerate with: python scripts/gen_functions_yaml.py\n"
    "# The Firebase CLI reads this instead of running discovery, so anything\n"
    "# missing here is silently dropped from the deploy no matter what the\n"
    "# decorators in main.py say.\n"
)


def main_() -> int:
    endpoints = {
        name: value
        for name, value in vars(main).items()
        if hasattr(value, "__firebase_endpoint__")
    }
    if not endpoints:
        print("no firebase endpoints found in main.py", file=sys.stderr)
        return 1

    yaml_text = serving.functions_as_yaml(endpoints)
    OUT.write_text(BANNER + yaml_text, encoding="utf-8", newline="\n")

    print(f"wrote {OUT} for endpoints: {sorted(endpoints)}")

    # The two options that were being dropped are the whole reason this script
    # exists, so fail loudly rather than shipping a manifest missing them.
    problems = []
    if "secretEnvironmentVariables" not in yaml_text:
        problems.append("no secretEnvironmentVariables - the API key will not be bound")
    if "timeoutSeconds" not in yaml_text:
        problems.append("no timeoutSeconds - the function falls back to the 60s default")
    for p in problems:
        print(f"  FAIL: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main_())
