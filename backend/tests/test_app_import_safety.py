import os
from pathlib import Path
import subprocess
import sys


def test_importing_app_creates_no_database_and_starts_no_thread(tmp_path):
    database_path = tmp_path / "must-not-exist.sqlite"
    backend_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(backend_root)
    environment["IOT_IDS_DATABASE_PATH"] = str(database_path)
    program = (
        "import threading; "
        "before={(t.name,t.ident) for t in threading.enumerate()}; "
        "import app; "
        "after={(t.name,t.ident) for t in threading.enumerate()}; "
        "assert not hasattr(app, 'app'); "
        "assert before == after"
    )

    result = subprocess.run(
        [sys.executable, "-B", "-c", program],
        cwd=backend_root,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not database_path.exists()
