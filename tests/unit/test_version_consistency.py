from pathlib import Path
import tomllib


def test_application_and_package_versions_are_consistent() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    main_source = Path("src/axiz/pe/sql_agent/main.py").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")
    assert f'version="{version}"' in main_source
    assert f"PoC {version}" in readme
