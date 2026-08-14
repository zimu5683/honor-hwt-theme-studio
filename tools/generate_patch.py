"""为上一个版本的 ZIP 便携包生成 bsdiff 补丁（仅 CI 端运行）。

用法：``python tools/generate_patch.py <current_zip> <current_tag>``

它会：
1. 通过 ``gh api`` 找到上一个正式 Release（排除当前 tag、prerelease 与 draft），
   并定位其 ``*-win64.zip`` 资产；
2. 下载该旧 ZIP；
3. 用 bsdiff4 生成补丁，写到 ``<repo 根>/dist/HwtThemeStudio-<prev>-<cur>-win64.patch``；
4. 把补丁元信息（name/sha256/from_sha256/target_sha256/url）写入
   ``patch-meta.json``，供 PowerShell 步骤并入 ``latest.json``。

找不到可用旧版时退出码 0 且不写任何元信息，客户端将回退到全量下载。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

import bsdiff4  # noqa: F401  — 仅 CI 端依赖


def _gh(*args: str) -> str:
    result = subprocess.run(
        ["gh", *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _previous_zip(repository: str, current_tag: str) -> tuple[str, str] | None:
    """返回 ``(tag, download_url)`` 上一个正式 Release 的 win64 ZIP。"""
    releases = json.loads(_gh(
        "api", f"repos/{repository}/releases",
        "--jq", '[.[] | select(.prerelease == false and .draft == false) | '
                '{tag: .tag_name, zip: ([.assets[] | select(.name | endswith("-win64.zip")) | .browser_download_url] | .[0])}]',
    ))
    for release in releases:
        tag = str(release.get("tag") or "")
        url = release.get("zip")
        if tag and tag != current_tag and url:
            return tag, str(url)
    return None


def _download(url: str, destination: Path) -> None:
    request = Request(url, headers={"Accept": "application/octet-stream", "User-Agent": "HwtThemeStudio-release"})
    with urlopen(request, timeout=180) as response, destination.open("wb") as handle:
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            handle.write(block)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: generate_patch.py <current_zip> <current_tag>", file=sys.stderr)
        return 2

    repository = os.environ.get("GITHUB_REPOSITORY", "zimu5683/honor-hwt-theme-studio")
    current_zip = Path(sys.argv[1])
    current_tag = sys.argv[2]
    if not current_zip.is_file() or not current_tag.startswith("v"):
        print("patch generation requires a built ZIP and a version tag", file=sys.stderr)
        return 2

    previous = _previous_zip(repository, current_tag)
    if previous is None:
        print("no previous release with a win64 ZIP; skipping patch", file=sys.stderr)
        return 0
    previous_tag, download_url = previous

    work = current_zip.parent / ".patch-work"
    work.mkdir(exist_ok=True)
    previous_zip = work / f"HwtThemeStudio-{previous_tag}-win64.zip"
    patch_name = f"HwtThemeStudio-{previous_tag}-{current_tag}-win64.patch"
    patch_path = current_zip.parent / patch_name
    try:
        if not previous_zip.is_file():
            _download(download_url, previous_zip)
        from_sha256 = _sha256(previous_zip)
        bsdiff4.file_diff(str(previous_zip), str(current_zip), str(patch_path))
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)

    base_url = f"https://github.com/{repository}/releases/download/{current_tag}"
    meta = {
        "name": patch_name,
        "url": f"{base_url}/{patch_name}",
        "sha256": _sha256(patch_path),
        "from_sha256": from_sha256,
        "target_sha256": _sha256(current_zip),
    }
    (current_zip.parent / "patch-meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(meta, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
