import ast

from scripts import clean_env_check as checker


def test_dependency_scan_resolves_local_script_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    research = tmp_path / "reports" / "research"
    research.mkdir(parents=True)
    (research / "helper.py").write_text("import numpy\n")
    (research / "runner.py").write_text("import helper\nimport missing_dependency\n")
    (tmp_path / "tracker.py").write_text('''
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent
RESEARCH = ROOT / "reports" / "research"
sys.path.insert(0, str(RESEARCH))
import helper
''')
    imports = checker._top_imports()
    assert "helper" not in imports
    assert "numpy" in imports
    assert "missing_dependency" in imports


def test_dependency_scan_does_not_execute_dynamic_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    tree = ast.parse('import sys\nsys.path.insert(0, dangerous())')
    assert checker._script_import_roots(tree, tmp_path / "runner.py") == {tmp_path}


def test_dependency_scan_does_not_accept_unrelated_nested_modules(tmp_path, monkeypatch):
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    nested = tmp_path / "unrelated"
    nested.mkdir()
    (nested / "missing_dependency.py").write_text("")
    (tmp_path / "runner.py").write_text("import missing_dependency\n")
    assert "missing_dependency" in checker._top_imports()


def test_dependency_scan_only_skips_imports_with_matching_handlers(tmp_path, monkeypatch):
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    (tmp_path / "runner.py").write_text('''
try:
    import required_dependency
except ValueError:
    pass
try:
    import also_required
finally:
    pass
try:
    import optional_dependency
except (ValueError, ImportError):
    pass
''')
    imports = checker._top_imports()
    assert "required_dependency" in imports
    assert "also_required" in imports
    assert "optional_dependency" not in imports
