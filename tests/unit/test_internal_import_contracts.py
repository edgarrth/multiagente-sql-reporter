from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_all_internal_imports_resolve_to_packaged_modules_or_symbols() -> None:
    project_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(project_root / "scripts" / "check_internal_imports.py")],
        cwd=project_root,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
