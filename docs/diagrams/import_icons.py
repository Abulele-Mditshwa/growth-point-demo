#!/usr/bin/env python3
"""Import the minimum official AWS/Azure SVG icon set used by the diagrams.

Normal mode extracts icons from official archives already downloaded under
``docs/_build/iconpacks`` and writes a checksum manifest. ``--check`` is fully
offline and validates the committed assets against that manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent
ARCHIVES = DOCS / "_build" / "iconpacks"
ICON_ROOT = HERE / "icons"
MANIFEST = ICON_ROOT / "manifest.json"

PACKS = {
    "aws": {
        "archive": "aws-icons.zip",
        "release": "AWS Architecture Icons 2026-07-31",
        "page_url": "https://aws.amazon.com/architecture/icons/",
        "download_url": "https://d1.awsstatic.com/onedam/marketing-channels/website/public/shared/architecture-icon-release/Icon-package_07312026.5846e92413caa21490223536cc97f1269e44fa92.zip",
    },
    "azure": {
        "archive": "azure-icons.zip",
        "release": "Azure Public Service Icons V24",
        "page_url": "https://learn.microsoft.com/en-us/azure/architecture/icons/",
        "download_url": "https://arch-center.azureedge.net/icons/Azure_Public_Service_Icons_V24.zip",
    },
    "entra": {
        "archive": "entra-icons.zip",
        "release": "Microsoft Entra architecture icons - Oct 2023",
        "page_url": "https://learn.microsoft.com/en-us/entra/architecture/architecture-icons",
        "download_url": "https://download.microsoft.com/download/3/1/a/31a56038-856a-4489-88e4-ee5a1c4352be/Microsoft%20Entra%20architecture%20icons%20-%20Oct%202023.zip",
    },
}

ASSETS = (
    ("aws", "users.svg", "Users/client", "Resource-Icons_07312026/Res_General-Icons/Res_48_Dark/Res_Authenticated-User_48_Dark.svg"),
    ("aws", "iam.svg", "AWS Identity and Access Management", "Architecture-Service-Icons_07312026/Arch_Security-Identity/64/Arch_AWS-Identity-and-Access-Management_64.svg"),
    ("aws", "s3.svg", "Amazon S3", "Architecture-Service-Icons_07312026/Arch_Storage/64/Arch_Amazon-Simple-Storage-Service_64.svg"),
    ("aws", "bedrock.svg", "Amazon Bedrock", "Architecture-Service-Icons_07312026/Arch_Artificial-Intelligence/64/Arch_Amazon-Bedrock_64.svg"),
    ("aws", "s3-vectors.svg", "Amazon S3 Vectors", "Resource-Icons_07312026/Res_Storage/Res_Amazon-Simple-Storage-Service_S3-Vectors_48.svg"),
    ("aws", "cloudwatch.svg", "Amazon CloudWatch", "Architecture-Service-Icons_07312026/Arch_Management-Tools/64/Arch_Amazon-CloudWatch_64.svg"),
    ("azure", "storage.svg", "Azure Blob Storage / ADLS Gen2", "Azure_Public_Service_Icons/Icons/storage/10086-icon-service-Storage-Accounts.svg"),
    ("azure", "ai-search.svg", "Azure AI Search", "Azure_Public_Service_Icons/Icons/ai + machine learning/10044-icon-service-Cognitive-Search.svg"),
    ("azure", "openai.svg", "Azure OpenAI", "Azure_Public_Service_Icons/Icons/ai + machine learning/03438-icon-service-Azure-OpenAI.svg"),
    ("azure", "app-service.svg", "Azure App Service", "Azure_Public_Service_Icons/Icons/app services/10035-icon-service-App-Services.svg"),
    ("azure", "managed-identity.svg", "Managed Identity", "Azure_Public_Service_Icons/Icons/identity/10227-icon-service-Entra-Managed-Identities.svg"),
    ("azure", "key-vault.svg", "Azure Key Vault", "Azure_Public_Service_Icons/Icons/security/10245-icon-service-Key-Vaults.svg"),
    ("azure", "monitor.svg", "Azure Monitor", "Azure_Public_Service_Icons/Icons/monitor/00001-icon-service-Monitor.svg"),
    ("azure", "app-insights.svg", "Application Insights", "Azure_Public_Service_Icons/Icons/monitor/00012-icon-service-Application-Insights.svg"),
    ("entra", "entra-id.svg", "Microsoft Entra ID", "Microsoft Entra architecture icons - Oct 2023/Microsoft Entra color icons SVG/Microsoft Entra ID color icon.svg"),
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_svg(path: Path) -> None:
    root = ElementTree.parse(path).getroot()
    if not root.tag.endswith("svg"):
        raise ValueError(f"Not an SVG root element: {path}")
    if not (root.get("viewBox") or (root.get("width") and root.get("height"))):
        raise ValueError(f"SVG has no dimensions: {path}")


def import_assets() -> None:
    entries = []
    for provider, local_name, service, member in ASSETS:
        pack = PACKS[provider]
        archive = ARCHIVES / pack["archive"]
        if not archive.exists():
            raise FileNotFoundError(
                f"Missing {archive}. Download it from {pack['download_url']} first."
            )
        local_provider = "azure" if provider == "entra" else provider
        destination = ICON_ROOT / local_provider / local_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as zf:
            destination.write_bytes(zf.read(member))
        parse_svg(destination)
        entries.append(
            {
                "provider": local_provider,
                "service": service,
                "release": pack["release"],
                "source_page": pack["page_url"],
                "download_url": pack["download_url"],
                "original_archive_path": member,
                "local_path": destination.relative_to(HERE).as_posix(),
                "sha256": digest(destination),
            }
        )
    payload = {
        "note": "Official provider SVGs copied without visual modification; filenames and locations only were normalized.",
        "assets": entries,
    }
    MANIFEST.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Imported {len(entries)} official icons and wrote {MANIFEST.relative_to(DOCS.parent)}")


def check_assets() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expected = {f"icons/{'azure' if p == 'entra' else p}/{name}" for p, name, _, _ in ASSETS}
    actual = {item["local_path"] for item in data["assets"]}
    if actual != expected:
        raise ValueError(f"Manifest asset set differs: missing={expected-actual}, extra={actual-expected}")
    for item in data["assets"]:
        path = HERE / item["local_path"]
        if not path.is_file():
            raise FileNotFoundError(path)
        parse_svg(path)
        actual_hash = digest(path)
        if actual_hash != item["sha256"]:
            raise ValueError(f"Checksum mismatch for {path}: {actual_hash}")
        if item["source_page"] not in {p["page_url"] for p in PACKS.values()}:
            raise ValueError(f"Unexpected source URL for {path}")
    print(f"ICON_CHECK_OK: {len(actual)} SVGs parsed; paths and SHA-256 checksums match")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Validate committed icons without network access")
    args = parser.parse_args()
    check_assets() if args.check else import_assets()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
