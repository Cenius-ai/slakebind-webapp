"""Shared fixtures for the Slakebind test suite."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "slakebind.py"


@pytest.fixture(scope="session")
def sb():
    """The tool imported as a module, for the fast pure-function tests."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import slakebind

    return slakebind


@pytest.fixture()
def home(tmp_path):
    """A private ledger folder; tests never touch the real ~/.slakebind."""
    return tmp_path / "sbhome"


@pytest.fixture()
def run(home):
    """Run the real CLI as a subprocess with the ledger pointed at `home`."""

    def _run(*args, stdin=None, env=None, timeout=30, with_home=True):
        environ = dict(os.environ)
        environ.pop("NO_COLOR", None)
        if with_home:
            environ["SLAKEBIND_HOME"] = str(home)
        if env:
            environ.update(env)
        kwargs = {
            "capture_output": True,
            "text": True,
            "env": environ,
            "cwd": str(ROOT),
            "timeout": timeout,
        }
        if stdin is None:
            kwargs["stdin"] = subprocess.DEVNULL
        else:
            kwargs["input"] = stdin
        return subprocess.run([sys.executable, str(SCRIPT), *args], **kwargs)

    return _run


def combined(result):
    """stdout plus stderr, so assertions about messages do not care which stream."""
    return (result.stdout or "") + (result.stderr or "")
