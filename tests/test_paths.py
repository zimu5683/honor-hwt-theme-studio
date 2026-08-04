from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hwtstudio.paths import data_dir


class PathTests(unittest.TestCase):
    def test_data_dir_falls_back_when_localappdata_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            expected_root = home / "AppData" / "Local" if os.name == "nt" else home / ".local" / "share"
            with (
                patch.dict(os.environ, {"LOCALAPPDATA": ""}, clear=False),
                patch("hwtstudio.paths.Path.home", return_value=home),
            ):
                result = data_dir()
            self.assertEqual(result, expected_root / "HwtThemeStudio")
            self.assertTrue(result.is_dir())

    def test_data_dir_rejects_relative_localappdata(self):
        if os.name != "nt":
            self.skipTest("LOCALAPPDATA 仅用于 Windows")
        with patch.dict(os.environ, {"LOCALAPPDATA": "relative-app-data"}, clear=False):
            with self.assertRaisesRegex(OSError, "必须是绝对路径"):
                data_dir()

    def test_data_dir_rejects_application_path_that_is_a_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "HwtThemeStudio").write_text("not a directory", encoding="utf-8")
            with patch.dict(os.environ, {"LOCALAPPDATA": str(root)}, clear=False):
                with self.assertRaisesRegex(OSError, "应用数据目录不是目录"):
                    data_dir()

    def test_data_dir_rejects_symlinked_application_directory(self):
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不支持符号链接")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            app_dir = root / "HwtThemeStudio"
            try:
                os.symlink(outside, app_dir, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建目录符号链接：{exc}")
            with patch.dict(os.environ, {"LOCALAPPDATA": str(root)}, clear=False):
                with self.assertRaisesRegex(OSError, "应用数据目录.*符号链接"):
                    data_dir()
            self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
