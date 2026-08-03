from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hwtstudio.updater import (
    ReleaseAsset,
    download_asset,
    is_newer_version,
    release_from_payload,
    safe_asset_name,
    select_update_asset,
)


class _FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.offset = 0
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.payload)
        block = self.payload[self.offset : self.offset + size]
        self.offset += len(block)
        return block


class UpdaterTests(unittest.TestCase):
    def test_numeric_version_comparison(self):
        self.assertTrue(is_newer_version("v0.1.10", "0.1.9"))
        self.assertFalse(is_newer_version("0.1.9", "0.1.10"))
        self.assertFalse(is_newer_version("0.1.10-beta.1", "0.1.10"))

    def test_release_payload_selects_desktop_asset_and_checksum(self):
        release = release_from_payload(
            {
                "version": "v0.2.0",
                "url": "https://github.com/zimu5683/honor-hwt-theme-studio/releases/tag/v0.2.0",
                "body": "修复更新流程",
                "assets": [
                    {"name": "HwtThemeReceiver-v0.2.0.apk", "url": "https://example.test/app.apk"},
                    {"name": "source.zip", "url": "https://example.test/source.zip"},
                    {
                        "name": "HwtThemeStudio-v0.2.0-win64.exe",
                        "url": "https://example.test/studio.exe",
                        "sha256": "a" * 64,
                    },
                    {
                        "name": "HwtThemeStudio-v0.2.0-win64.exe.sha256",
                        "url": "https://example.test/studio.exe.sha256",
                    },
                ],
            }
        )
        self.assertEqual(release.version, "v0.2.0")
        self.assertEqual(release.body, "修复更新流程")
        self.assertIsNotNone(release.asset)
        self.assertEqual(release.asset.name, "HwtThemeStudio-v0.2.0-win64.exe")
        self.assertEqual(release.asset.sha256, "a" * 64)

    def test_asset_selection_rejects_non_desktop_files(self):
        assets = [
            ReleaseAsset("source.zip", "https://example.test/source.zip"),
            ReleaseAsset("HwtThemeReceiver-v0.2.0.apk", "https://example.test/app.apk"),
            ReleaseAsset("HwtThemeStudio-v0.2.0-win64.exe", "https://example.test/studio.exe"),
        ]
        selected = select_update_asset(assets)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.name, "HwtThemeStudio-v0.2.0-win64.exe")

    def test_github_api_payload_prefers_browser_download_url(self):
        release = release_from_payload(
            {
                "tag_name": "v0.2.0",
                "assets": [
                    {
                        "name": "HwtThemeStudio-v0.2.0-win64.exe",
                        "url": "https://api.github.com/assets/1",
                        "browser_download_url": "https://github.com/download/studio.exe",
                        "sha256": "b" * 64,
                    }
                ],
            }
        )
        self.assertEqual(release.asset.url, "https://github.com/download/studio.exe")

    def test_safe_asset_name_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            safe_asset_name("..\\outside.exe")
        with self.assertRaises(ValueError):
            safe_asset_name("C:\\outside.exe")

    def test_download_asset_verifies_sha256_before_commit(self):
        payload = b"verified update payload"
        checksum = hashlib.sha256(payload).hexdigest()
        release = release_from_payload(
            {
                "version": "v0.2.0",
                "assets": [
                    {
                        "name": "HwtThemeStudio-v0.2.0-win64.exe",
                        "url": "https://example.test/studio.exe",
                        "sha256": checksum,
                    }
                ],
            }
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "hwtstudio.updater.urllib.request.urlopen", return_value=_FakeResponse(payload)
        ):
            path = download_asset(release, download_dir=Path(directory))
            self.assertEqual(path.read_bytes(), payload)
            self.assertFalse(path.with_name(path.name + ".part").exists())


if __name__ == "__main__":
    unittest.main()
