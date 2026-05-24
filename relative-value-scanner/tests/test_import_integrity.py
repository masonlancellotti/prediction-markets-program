import ast
import importlib.util
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _relative_value_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "relative_value" or alias.name.startswith("relative_value."):
                    imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "relative_value" or node.module.startswith("relative_value."):
                imports.add(node.module)
    return imports


def test_scan_imports_without_running_live_fetches() -> None:
    spec = importlib.util.spec_from_file_location("scan_import_smoke", PROJECT_ROOT / "scan.py")

    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert hasattr(module, "main")


def test_relative_value_import_targets_exist_in_package() -> None:
    source_files = [PROJECT_ROOT / "scan.py"]
    source_files.extend((PROJECT_ROOT / "relative_value").glob("*.py"))

    missing: list[str] = []
    for source_file in source_files:
        for module_name in _relative_value_imports(source_file):
            if module_name == "relative_value":
                continue
            module_path = PROJECT_ROOT.joinpath(*module_name.split(".")).with_suffix(".py")
            package_path = PROJECT_ROOT.joinpath(*module_name.split("."), "__init__.py")
            if not module_path.exists() and not package_path.exists():
                missing.append(f"{source_file.relative_to(PROJECT_ROOT)} imports {module_name}")

    assert missing == []


def test_sports_scope_modules_imported_by_scan_exist() -> None:
    scan_imports = _relative_value_imports(PROJECT_ROOT / "scan.py")

    assert "relative_value.mlb_same_scope_audit" in scan_imports
    assert "relative_value.mlb_world_series_execution_diagnostics" in scan_imports
    assert "relative_value.nhl_same_scope" in scan_imports

    assert (PROJECT_ROOT / "relative_value" / "mlb_same_scope_audit.py").exists()
    assert (PROJECT_ROOT / "relative_value" / "mlb_world_series_execution_diagnostics.py").exists()
    assert (PROJECT_ROOT / "relative_value" / "nba_scope.py").exists()
    assert (PROJECT_ROOT / "relative_value" / "nhl_same_scope.py").exists()
    assert (PROJECT_ROOT / "relative_value" / "nhl_scope.py").exists()
