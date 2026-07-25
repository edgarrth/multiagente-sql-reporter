from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Microsoft Teams application package")
    parser.add_argument("--bot-id", required=True, help="Microsoft Entra / Azure Bot application ID")
    parser.add_argument("--output", default="teams_adapter/appPackage/axiz-sql-agent-teams.zip")
    args = parser.parse_args()

    package_dir = Path("teams_adapter/appPackage")
    template = package_dir / "manifest.template.json"
    manifest = json.loads(template.read_text(encoding="utf-8").replace("${TEAMS_BOT_ID}", args.bot_id))
    generated_manifest = package_dir / "manifest.json"
    generated_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(generated_manifest, "manifest.json")
        archive.write(package_dir / "outline.png", "outline.png")
        archive.write(package_dir / "color.png", "color.png")
    generated_manifest.unlink()
    print(output)


if __name__ == "__main__":
    main()
