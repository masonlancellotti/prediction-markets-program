from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable


REQUIRED_IMPORTS: tuple[tuple[str, str], ...] = (
    ("sqlalchemy", "SQLAlchemy"),
    ("pandas", "pandas"),
    ("numpy", "numpy"),
    ("requests", "requests"),
    ("pydantic", "pydantic"),
    ("dotenv", "python-dotenv"),
    ("sklearn", "scikit-learn"),
    ("streamlit", "streamlit"),
    ("plotly", "plotly"),
    ("pytest", "pytest"),
    ("cryptography", "cryptography"),
    ("yaml", "pyyaml"),
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _venv_python(root: Path) -> Path:
    return root / ".venv" / "Scripts" / "python.exe"


def _python_cmd_shim(root: Path) -> Path:
    return root / "python.cmd"


def _pytest_cmd_shim(root: Path) -> Path:
    return root / "pytest.cmd"


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


def _pip_status() -> dict[str, object]:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception as exc:
        return {
            "available": False,
            "detail": f"{type(exc).__name__}: {exc}",
        }
    output = (result.stdout or result.stderr).strip()
    return {
        "available": result.returncode == 0,
        "detail": output,
    }


def _powershell_command_source(command: str, cwd: Path) -> str:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        return ""
    script = (
        f"$cmd = Get-Command {command} -ErrorAction SilentlyContinue | Select-Object -First 1; "
        "if ($null -eq $cmd) { '' } "
        "elseif ($cmd.Path) { $cmd.Path } "
        "elseif ($cmd.Source) { $cmd.Source } "
        "else { $cmd.Name }"
    )
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-Command", script],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return ""
    return (result.stdout or result.stderr).strip()


def check_imports(required_imports: Iterable[tuple[str, str]] = REQUIRED_IMPORTS) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    for module_name, package_name in required_imports:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            checks.append(
                {
                    "module": module_name,
                    "package": package_name,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        else:
            checks.append(
                {
                    "module": module_name,
                    "package": package_name,
                    "ok": True,
                    "error": None,
                }
            )
    return checks


def build_report(
    root: Path | None = None,
    required_imports: Iterable[tuple[str, str]] = REQUIRED_IMPORTS,
) -> dict[str, object]:
    repo_root = (root or _repo_root()).resolve()
    venv_python = _venv_python(repo_root)
    python_cmd = _python_cmd_shim(repo_root)
    pytest_cmd = _pytest_cmd_shim(repo_root)
    requirements = repo_root / "requirements.txt"
    imports = check_imports(required_imports)
    missing = [item for item in imports if not item["ok"]]
    active_is_repo_venv = _same_path(Path(sys.executable), venv_python)
    powershell_python_source = _powershell_command_source("python", repo_root)
    powershell_python_uses_repo_shim = bool(powershell_python_source) and _same_path(Path(powershell_python_source), python_cmd)

    if missing and venv_python.exists() and powershell_python_uses_repo_shim:
        recommendation = r"From this directory, use python main.py trading-readiness --last-days 7."
    elif missing and venv_python.exists() and python_cmd.exists():
        recommendation = (
            r"PowerShell is not resolving the local python.cmd shim for bare 'python'. "
            r"Use .\python.cmd main.py trading-readiness --last-days 7 or .\scripts\run.ps1 trading-readiness --last-days 7."
        )
    elif missing and venv_python.exists() and not active_is_repo_venv:
        recommendation = (
            r"Use the repo venv wrapper: .\scripts\run.ps1 trading-readiness --last-days 7 "
            r"or run .\.venv\Scripts\python.exe scripts\env_doctor.py"
        )
    elif missing and venv_python.exists():
        recommendation = r"Repair the repo venv: .\scripts\setup-dev.ps1"
    elif missing:
        recommendation = r"Create the repo venv and install dependencies: .\scripts\setup-dev.ps1"
    elif python_cmd.exists() and not powershell_python_uses_repo_shim:
        recommendation = (
            r"Environment imports are OK for this interpreter, but PowerShell bare 'python' is not using the repo shim. "
            r"Use .\python.cmd main.py trading-readiness --last-days 7 or .\scripts\run.ps1 trading-readiness --last-days 7."
        )
    else:
        recommendation = r"Environment looks usable. Prefer .\scripts\run.ps1 <command> and .\scripts\test.ps1."

    return {
        "repo_root": str(repo_root),
        "sys_executable": sys.executable,
        "sys_version": sys.version.replace("\n", " "),
        "cwd": os.getcwd(),
        "virtual_env": os.environ.get("VIRTUAL_ENV") or "",
        "repo_venv_python": str(venv_python),
        "repo_venv_python_exists": venv_python.exists(),
        "active_is_repo_venv": active_is_repo_venv,
        "python_cmd_shim": str(python_cmd),
        "python_cmd_shim_exists": python_cmd.exists(),
        "pytest_cmd_shim": str(pytest_cmd),
        "pytest_cmd_shim_exists": pytest_cmd.exists(),
        "powershell_bare_python_source": powershell_python_source,
        "powershell_bare_python_uses_repo_shim": powershell_python_uses_repo_shim,
        "pip": _pip_status(),
        "requirements_txt": str(requirements),
        "requirements_txt_exists": requirements.exists(),
        "imports": imports,
        "missing_modules": [str(item["module"]) for item in missing],
        "recommended_next_command": recommendation,
    }


def format_report(report: dict[str, object]) -> str:
    lines = [
        "Kalshi Weather Edge environment doctor",
        f"repo_root: {report['repo_root']}",
        f"sys.executable: {report['sys_executable']}",
        f"sys.version: {report['sys_version']}",
        f"cwd: {report['cwd']}",
        f"VIRTUAL_ENV: {report['virtual_env'] or '(not set)'}",
        f".venv/Scripts/python.exe: {report['repo_venv_python']}",
        f".venv python exists: {report['repo_venv_python_exists']}",
        f"active interpreter is repo .venv: {report['active_is_repo_venv']}",
        f"repo python.cmd shim: {report['python_cmd_shim']}",
        f"repo python.cmd shim exists: {report['python_cmd_shim_exists']}",
        f"repo pytest.cmd shim: {report['pytest_cmd_shim']}",
        f"repo pytest.cmd shim exists: {report['pytest_cmd_shim_exists']}",
        f"PowerShell bare python resolves to: {report['powershell_bare_python_source'] or '(unknown)'}",
        f"PowerShell bare python uses repo shim: {report['powershell_bare_python_uses_repo_shim']}",
        f"python -m pip available: {report['pip']['available']}",
        f"python -m pip detail: {report['pip']['detail'] or '(none)'}",
        f"requirements.txt: {report['requirements_txt']}",
        f"requirements.txt exists: {report['requirements_txt_exists']}",
        "required package imports:",
    ]
    for item in report["imports"]:
        status = "OK" if item["ok"] else "MISSING"
        suffix = "" if item["ok"] else f" - {item['error']}"
        lines.append(f"  {status}: {item['package']} ({item['module']}){suffix}")

    missing = report["missing_modules"]
    lines.append(f"missing modules: {', '.join(missing) if missing else '(none)'}")
    lines.append(f"recommended next command: {report['recommended_next_command']}")
    return "\n".join(lines)


def main() -> int:
    report = build_report()
    print(format_report(report))
    return 1 if report["missing_modules"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
