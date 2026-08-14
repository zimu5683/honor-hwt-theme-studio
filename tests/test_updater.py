from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from hwtstudio.updater import (
    _sha256,
    _fetch_json,
    _extract_portable_archive,
    APP_NAME,
    PORTABLE_EXECUTABLE_NAME,
    ReleaseAsset,
    VerifiedDownload,
    download_asset,
    is_newer_version,
    is_windows_setup_asset,
    launch_update,
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


class _CancelAtEofResponse(_FakeResponse):
    def __init__(self, payload: bytes, cancelled: threading.Event):
        super().__init__(payload)
        self.cancelled = cancelled

    def read(self, size: int = -1) -> bytes:
        block = super().read(size)
        if not block:
            self.cancelled.set()
        return block


class UpdaterTests(unittest.TestCase):
    def test_cached_update_hashing_honors_cancellation(self):
        cancelled = threading.Event()
        cancelled.set()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cached.exe"
            path.write_bytes(b"cached update")
            with self.assertRaisesRegex(RuntimeError, "取消"):
                _sha256(path, cancelled=cancelled)

    def test_cancelled_update_is_rejected_before_network_work(self):
        cancelled = threading.Event()
        cancelled.set()
        release = release_from_payload(
            {
                "version": "v0.2.0",
                "assets": [
                    {
                        "name": "HwtThemeStudio-v0.2.0-win64.exe",
                        "url": "https://example.test/studio.exe",
                        "sha256": "a" * 64,
                    }
                ],
            }
        )
        with patch("hwtstudio.updater.urllib.request.urlopen") as urlopen:
            with self.assertRaisesRegex(RuntimeError, "取消"):
                download_asset(release, download_dir=Path(tempfile.gettempdir()), cancelled=cancelled)
        urlopen.assert_not_called()

    def test_numeric_version_comparison(self):
        self.assertTrue(is_newer_version("v0.1.10", "0.1.9"))
        self.assertFalse(is_newer_version("0.1.9", "0.1.10"))
        self.assertFalse(is_newer_version("0.1.10-beta.1", "0.1.10"))

    def test_release_text_is_bounded_for_ui(self):
        release = release_from_payload({"version": "v0.2.0", "body": "x" * 20_000})
        self.assertLessEqual(len(release.body), 12_030)
        self.assertTrue(release.body.endswith("（更新说明过长，已截断）"))
        with self.assertRaisesRegex(ValueError, "版本号过长"):
            release_from_payload({"version": "v" + "1" * 64})

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

    def test_asset_selection_prefers_setup_over_portable_zip_and_legacy_exe(self):
        assets = [
            ReleaseAsset("HwtThemeStudio-v0.2.0-win64.exe", "https://example.test/studio.exe"),
            ReleaseAsset("HwtThemeStudio-v0.2.0-win64.zip", "https://example.test/studio.zip"),
            ReleaseAsset("HwtThemeStudio-v0.2.0-win64-Setup.exe", "https://example.test/setup.exe"),
        ]
        selected = select_update_asset(assets)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.name, "HwtThemeStudio-v0.2.0-win64-Setup.exe")

    def test_asset_selection_keeps_zip_as_pre_setup_fallback(self):
        assets = [
            ReleaseAsset("HwtThemeStudio-v0.2.0-win64.exe", "https://example.test/studio.exe"),
            ReleaseAsset("HwtThemeStudio-v0.2.0-win64.zip", "https://example.test/studio.zip"),
        ]
        selected = select_update_asset(assets)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.name, "HwtThemeStudio-v0.2.0-win64.zip")

    def test_setup_asset_name_is_explicit(self):
        self.assertTrue(is_windows_setup_asset("HwtThemeStudio-v0.2.0-win64-Setup.exe"))
        self.assertFalse(is_windows_setup_asset("HwtThemeStudio-v0.2.0-win64.exe"))
        self.assertFalse(is_windows_setup_asset("HwtThemeStudio-v0.2.0-win64.zip"))

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

    def test_update_metadata_rejects_oversized_response(self):
        payload = b"{}" + b"x" * (2 * 1024 * 1024)
        with patch(
            "hwtstudio.updater.urllib.request.urlopen", return_value=_FakeResponse(payload)
        ):
            with self.assertRaisesRegex(ValueError, "响应过大"):
                _fetch_json("https://example.test/latest.json")

    def test_safe_asset_name_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            safe_asset_name("..\\outside.exe")
        with self.assertRaises(ValueError):
            safe_asset_name("C:\\outside.exe")
        with self.assertRaises(ValueError):
            safe_asset_name("studio.exe\n")
        with self.assertRaises(ValueError):
            safe_asset_name(" studio.exe")
        with self.assertRaisesRegex(ValueError, "过长"):
            safe_asset_name("a" * 197 + ".exe")

    def test_update_requests_reject_insecure_http(self):
        from hwtstudio.updater import _request

        with self.assertRaisesRegex(ValueError, "HTTPS"):
            _request("http://example.test/studio.exe")

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
            download = download_asset(release, download_dir=Path(directory))
            self.assertIsInstance(download, VerifiedDownload)
            self.assertEqual(download.path.read_bytes(), payload)
            self.assertEqual(download.sha256, checksum)
            self.assertEqual(list(Path(directory).glob(".*.part")), [])

    def test_cached_download_preserves_verified_sha256(self):
        payload = b"cached verified update payload"
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
            "hwtstudio.updater.urllib.request.urlopen"
        ) as urlopen:
            target = Path(directory) / release.asset.name
            target.write_bytes(payload)
            download = download_asset(release, download_dir=Path(directory))
            self.assertEqual(download, VerifiedDownload(path=target, sha256=checksum))
            urlopen.assert_not_called()

    def test_download_rejects_symlinked_cached_target(self):
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不支持符号链接")
        payload = b"cached target"
        checksum = hashlib.sha256(payload).hexdigest()
        release = release_from_payload(
            {
                "version": "v0.2.0",
                "assets": [{
                    "name": "HwtThemeStudio-v0.2.0-win64.exe",
                    "url": "https://example.test/studio.exe",
                    "sha256": checksum,
                }],
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside.exe"
            outside.write_bytes(payload)
            target = root / release.asset.name
            try:
                os.symlink(outside, target)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建符号链接：{exc}")
            with patch("hwtstudio.updater.urllib.request.urlopen") as urlopen:
                with self.assertRaisesRegex(ValueError, "符号链接"):
                    download_asset(release, download_dir=root)
            urlopen.assert_not_called()

    def test_download_rejects_symlinked_cache_directory(self):
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不支持符号链接")
        payload = b"cache directory target"
        checksum = hashlib.sha256(payload).hexdigest()
        release = release_from_payload(
            {
                "version": "v0.2.0",
                "assets": [{
                    "name": "HwtThemeStudio-v0.2.0-win64.exe",
                    "url": "https://example.test/studio.exe",
                    "sha256": checksum,
                }],
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            cache = root / "updates"
            try:
                os.symlink(outside, cache, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建目录符号链接：{exc}")
            with patch("hwtstudio.updater.urllib.request.urlopen") as urlopen:
                with self.assertRaisesRegex(ValueError, "缓存目录.*符号链接"):
                    download_asset(release, download_dir=cache)
            urlopen.assert_not_called()
            self.assertEqual(list(outside.iterdir()), [])

    def test_download_rejects_symlinked_cache_parent(self):
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不支持符号链接")
        payload = b"cache parent target"
        checksum = hashlib.sha256(payload).hexdigest()
        release = release_from_payload(
            {
                "version": "v0.2.0",
                "assets": [{
                    "name": "HwtThemeStudio-v0.2.0-win64.exe",
                    "url": "https://example.test/studio.exe",
                    "sha256": checksum,
                }],
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            link = root / "linked-root"
            try:
                os.symlink(outside, link, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建目录符号链接：{exc}")
            cache = link / "updates"
            with patch("hwtstudio.updater.urllib.request.urlopen") as urlopen:
                with self.assertRaisesRegex(ValueError, "缓存目录的父路径.*符号链接"):
                    download_asset(release, download_dir=cache)
            urlopen.assert_not_called()
            self.assertFalse((outside / "updates").exists())

    def test_portable_archive_extracts_expected_layout(self):
        payload = b"portable executable"
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "studio.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr(f"{APP_NAME}/", b"")
                bundle.writestr(f"{APP_NAME}/{PORTABLE_EXECUTABLE_NAME}", payload)
            app_dir, staging_root = _extract_portable_archive(archive)
            self.assertEqual(app_dir.name, APP_NAME)
            self.assertEqual((app_dir / PORTABLE_EXECUTABLE_NAME).read_bytes(), payload)
            self.assertEqual(staging_root.parent, archive.parent)

    def test_portable_archive_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "studio.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../outside.txt", b"escape")
            with self.assertRaisesRegex(ValueError, "路径穿越"):
                _extract_portable_archive(archive)
            self.assertFalse((Path(directory).parent / "outside.txt").exists())

    def test_launch_portable_archive_after_hash_verification(self):
        payload = b"portable executable"
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "studio.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr(f"{APP_NAME}/{PORTABLE_EXECUTABLE_NAME}", payload)
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            download = VerifiedDownload(path=archive, sha256=checksum)
            with patch("hwtstudio.updater.subprocess.Popen") as popen:
                self.assertFalse(launch_update(download))
            popen.assert_called_once()
            launched = Path(popen.call_args.args[0][0])
            self.assertEqual(launched.name, PORTABLE_EXECUTABLE_NAME)
            self.assertTrue(launched.is_file())

    def test_portable_update_replaces_running_dir_in_place(self):
        """便携版更新必须原地替换当前 exe 所在目录，绝不新建 APP_NAME 子目录。"""
        payload = b"portable executable"
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "studio.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr(f"{APP_NAME}/{PORTABLE_EXECUTABLE_NAME}", payload)
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            download = VerifiedDownload(path=archive, sha256=checksum)

            # 模拟已安装的便携版：当前 exe 位于一个名为 APP_NAME 的目录内。
            install_root = Path(directory) / "installed"
            app_dir = install_root / APP_NAME
            app_dir.mkdir(parents=True)
            running_exe = app_dir / PORTABLE_EXECUTABLE_NAME
            running_exe.write_bytes(b"old")

            with (
                patch("hwtstudio.updater.os.name", "nt"),
                patch("hwtstudio.updater.sys.frozen", True, create=True),
                patch("hwtstudio.updater.subprocess.Popen") as popen,
                patch("hwtstudio.updater._launch_portable_update", return_value=True) as launch,
            ):
                import hwtstudio.updater as updater
                with patch.object(updater.sys, "executable", str(running_exe)):
                    self.assertTrue(launch_update(download))

            launch.assert_called_once()
            target_dir = launch.call_args.args[2]
            self.assertEqual(target_dir, app_dir.resolve())
            # 更新目标就是当前 exe 的父目录，而不是父目录之下再套一层 APP_NAME。
            self.assertNotEqual(target_dir, app_dir / APP_NAME)

    def test_launch_setup_directly_without_legacy_replacement(self):
        payload = b"verified setup"
        with tempfile.TemporaryDirectory() as directory:
            setup = Path(directory) / "HwtThemeStudio-v0.2.0-win64-Setup.exe"
            setup.write_bytes(payload)
            download = VerifiedDownload(path=setup, sha256=hashlib.sha256(payload).hexdigest())
            with (
                patch("hwtstudio.updater.os.name", "nt"),
                patch("hwtstudio.updater.sys.frozen", True, create=True),
                patch("hwtstudio.updater.subprocess.Popen") as popen,
                patch("hwtstudio.updater._spawn_encoded_powershell") as spawn_helper,
            ):
                self.assertTrue(launch_update(download))
            popen.assert_called_once_with([str(setup.absolute())])
            spawn_helper.assert_not_called()

    def test_launch_legacy_exe_keeps_replacement_helper(self):
        payload = b"verified legacy executable"
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "HwtThemeStudio-v0.2.0-win64.exe"
            executable.write_bytes(payload)
            download = VerifiedDownload(path=executable, sha256=hashlib.sha256(payload).hexdigest())
            with (
                patch("hwtstudio.updater.os.name", "nt"),
                patch("hwtstudio.updater.sys.frozen", True, create=True),
                patch("hwtstudio.updater.subprocess.Popen") as popen,
                patch("hwtstudio.updater._spawn_encoded_powershell") as spawn_helper,
            ):
                self.assertTrue(launch_update(download))
            popen.assert_not_called()
            spawn_helper.assert_called_once()

    def test_launch_update_rejects_file_changed_after_download(self):
        payload = b"verified update payload"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hwtstudio-test-update.exe"
            path.write_bytes(payload)
            download = VerifiedDownload(path=path, sha256=hashlib.sha256(payload).hexdigest())
            path.write_bytes(b"tampered update payload")
            with patch("hwtstudio.updater.subprocess.Popen") as popen:
                with self.assertRaisesRegex(ValueError, "启动前.*SHA-256"):
                    launch_update(download)
            popen.assert_not_called()

    def test_launch_update_rejects_symlinked_download(self):
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不支持符号链接")
        payload = b"verified update payload"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside.exe"
            outside.write_bytes(payload)
            path = root / "hwtstudio-test-update.exe"
            try:
                os.symlink(outside, path)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建符号链接：{exc}")
            download = VerifiedDownload(path=path, sha256=hashlib.sha256(payload).hexdigest())
            with patch("hwtstudio.updater.subprocess.Popen") as popen:
                with self.assertRaisesRegex(ValueError, "符号链接"):
                    launch_update(download)
            popen.assert_not_called()

    def test_download_asset_rejects_truncated_declared_response(self):
        payload = b"truncated update payload"
        checksum = hashlib.sha256(payload).hexdigest()
        release = release_from_payload(
            {
                "version": "v0.2.0",
                "assets": [{
                    "name": "HwtThemeStudio-v0.2.0-win64.exe",
                    "url": "https://example.test/studio.exe",
                    "sha256": checksum,
                }],
            }
        )
        response = _FakeResponse(payload)
        response.headers = {"Content-Length": str(len(payload) + 1)}
        with tempfile.TemporaryDirectory() as directory, patch(
            "hwtstudio.updater.urllib.request.urlopen", return_value=response
        ):
            with self.assertRaisesRegex(ValueError, "长度与声明不一致"):
                download_asset(release, download_dir=Path(directory))
            self.assertEqual(list(Path(directory).glob(".*.part")), [])

    def test_cancelled_after_download_cleans_private_partial(self):
        payload = b"cancelled update payload"
        cancelled = threading.Event()
        checksum = hashlib.sha256(payload).hexdigest()
        release = release_from_payload(
            {
                "version": "v0.2.0",
                "assets": [{
                    "name": "HwtThemeStudio-v0.2.0-win64.exe",
                    "url": "https://example.test/studio.exe",
                    "sha256": checksum,
                }],
            }
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "hwtstudio.updater.urllib.request.urlopen",
            return_value=_CancelAtEofResponse(payload, cancelled),
        ):
            with self.assertRaisesRegex(RuntimeError, "取消"):
                download_asset(release, download_dir=Path(directory), cancelled=cancelled)
            self.assertEqual(list(Path(directory).glob(".*.part")), [])


if __name__ == "__main__":
    unittest.main()
