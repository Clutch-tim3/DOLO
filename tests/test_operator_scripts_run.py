"""
The operator scripts still run.

`manage_users.py` imported MOCK_CLIENT_REGISTRY. When that dict was removed in
B4 the script broke — `create` died on ImportError — and the whole suite stayed
green, because nothing here executed a script. Provisioning an account, which
is the only way anyone gets into the product, was broken for a commit.

These are cheap: each script is invoked as a subprocess with --help, which
exercises every module-level import and the argument parser without touching
data. That is the failure that actually happened.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SCRIPTS = [
    "scripts/manage_users.py",
    "scripts/manage_companies.py",
    "scripts/manage_invites.py",
    "scripts/migrate_company_archive.py",
    "scripts/purge_test_data.py",
    "scripts/sync_firebase_public.py",
    "ops/smoke_check.py",
]


@pytest.mark.parametrize("script", SCRIPTS)
def test_the_script_imports_and_parses_arguments(script):
    path = PROJECT_ROOT / script
    assert path.exists(), f"{script} is referenced but missing"

    result = subprocess.run(
        [sys.executable, str(path), "--help"],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=180,
    )

    # --help exits 0 for argparse. A script with no parser may exit non-zero,
    # but an ImportError shows up as a traceback either way, and that is the
    # thing being caught.
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined, f"{script} failed to import:\n{combined[-800:]}"
    assert "ImportError" not in combined
    assert "ModuleNotFoundError" not in combined


@pytest.mark.parametrize("script,subcommands", [
    ("scripts/manage_users.py", ("create", "list", "set-password", "disable", "enable")),
    ("scripts/manage_companies.py", ("create", "list", "set-tier", "suspend", "activate")),
    ("scripts/manage_invites.py", ("create", "list", "revoke", "reset", "resets")),
])
def test_the_documented_subcommands_exist(script, subcommands):
    """
    The runbook names these. A renamed subcommand is a runbook that lies at the
    moment someone is following it because something is already wrong.
    """
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / script), "--help"],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=180,
    )
    combined = result.stdout + result.stderr
    for name in subcommands:
        assert name in combined, f"{script} no longer offers '{name}'"
