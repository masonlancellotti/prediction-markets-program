from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_DOCTOR_PATH = PROJECT_ROOT / "scripts" / "env_doctor.py"
README_PATH = PROJECT_ROOT / "README.md"
PYTHON_CMD_PATH = PROJECT_ROOT / "python.cmd"
PYTEST_CMD_PATH = PROJECT_ROOT / "pytest.cmd"


def _load_env_doctor():
    spec = importlib.util.spec_from_file_location("env_doctor", ENV_DOCTOR_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_env_doctor_can_run_and_prints_active_interpreter():
    result = subprocess.run(
        [sys.executable, str(ENV_DOCTOR_PATH)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert "sys.executable:" in result.stdout
    assert str(Path(sys.executable)) in result.stdout
    assert "recommended next command:" in result.stdout


def test_env_doctor_reports_missing_modules_without_crashing():
    env_doctor = _load_env_doctor()

    report = env_doctor.build_report(
        PROJECT_ROOT,
        required_imports=(("definitely_missing_kalshi_weather_edge_package", "missing-test-package"),),
    )
    text = env_doctor.format_report(report)

    assert report["missing_modules"] == ["definitely_missing_kalshi_weather_edge_package"]
    assert "MISSING: missing-test-package" in text
    assert "recommended next command:" in text


def test_readme_references_stable_operator_scripts():
    text = README_PATH.read_text(encoding="utf-8")

    assert r".\scripts\setup-dev.ps1" in text
    assert r".\python.cmd main.py trading-readiness --last-days 7" in text
    assert r".\scripts\run.ps1 trading-readiness --last-days 7" in text
    assert r".\scripts\test.ps1" in text


def test_root_python_shims_delegate_to_repo_venv_without_bare_python_recursion():
    python_cmd = PYTHON_CMD_PATH.read_text(encoding="utf-8")
    pytest_cmd = PYTEST_CMD_PATH.read_text(encoding="utf-8")

    assert r".venv\Scripts\python.exe" in python_cmd
    assert r'"%VENV_PYTHON%" %*' in python_cmd
    assert r".venv\Scripts\python.exe" in pytest_cmd
    assert r'"%VENV_PYTHON%" -m pytest %*' in pytest_cmd
    assert "Run .\\scripts\\setup-dev.ps1" in python_cmd
    assert "Run .\\scripts\\setup-dev.ps1" in pytest_cmd
    assert not re.search(r"(?im)^\s*python(\.exe)?\b", python_cmd)
    assert not re.search(r"(?im)^\s*python(\.exe)?\b", pytest_cmd)


def test_env_doctor_reports_root_shim_status():
    env_doctor = _load_env_doctor()

    report = env_doctor.build_report(
        PROJECT_ROOT,
        required_imports=(("sys", "sys"),),
    )
    text = env_doctor.format_report(report)

    assert report["python_cmd_shim_exists"] is True
    assert report["pytest_cmd_shim_exists"] is True
    assert "repo python.cmd shim exists: True" in text
    assert "PowerShell bare python uses repo shim:" in text
