"""Record the runtime and document-tool versions used by a research rebuild."""

import hashlib
import platform
import shutil
import sqlite3
import subprocess
from pathlib import Path


def _tool_lock(name: str) -> dict | None:
    executable = shutil.which(name)
    if not executable:
        return None
    path = Path(executable).resolve()
    flag = "-v" if name in {"pdftotext", "pdfimages"} else "--version"
    result = subprocess.run(
        [str(path), flag], check=True, capture_output=True, text=True, timeout=15,
    )
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "version": ((result.stdout + result.stderr).splitlines() or [""])[0],
    }


def environment_lock(*, include_pdf_images: bool = True) -> dict:
    """Identify the runtime and external tool files involved in scientific execution."""
    tools = ("pdftotext", "tesseract", "pdfimages") if include_pdf_images else ("pdftotext", "tesseract")
    return {
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "sqlite": sqlite3.sqlite_version,
        "platform": platform.system(),
        "architecture": platform.machine(),
        "tools": {name: _tool_lock(name) for name in tools},
    }
