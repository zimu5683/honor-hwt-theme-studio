from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .paths import data_dir


DEFAULT_REPOSITORY = "zimu5683/honor-hwt-theme-studio"
DEFAULT_LATEST_JSON_URL = (
    "https://github.com/zimu5683/honor-hwt-theme-studio/releases/latest/download/latest.json"
)
DEFAULT_RELEASES_API_URL = f"https://api.github.com/repos/{DEFAULT_REPOSITORY}/releases/latest"
MAX_DOWNLOAD_SIZE = 512 * 1024 * 1024
ProgressCallback = Callable[[int, int, str], None]


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    sha256: str = ""


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    body: str
    asset: ReleaseAsset | None


@dataclass(frozen=True)
class UpdateCheck:
    current_version: str
    latest_version: str | None
    release: Release | None
    update_available: bool


def parse_version_tag(value: str) -> tuple[int, ...]:
    normalized = value.strip().lstrip("vV")
    match = re.match(r"^(\d+(?:\.\d+)*)(?:[-+].*)?$", normalized)
    if not match:
        raise ValueError(f"无效版本号：{value}")
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer_version(candidate: str, current: str) -> bool:
    left = list(parse_version_tag(candidate))
    right = list(parse_version_tag(current))
    length = max(len(left), len(right))
    left.extend([0] * (length - len(left)))
    right.extend([0] * (length - len(right)))
    return left > right


def _payload_assets(payload: dict[str, Any]) -> list[ReleaseAsset]:
    raw_assets = payload.get("assets")
    if not isinstance(raw_assets, list):
        return []
    assets: list[ReleaseAsset] = []
    for raw in raw_assets:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        url = raw.get("browser_download_url") or raw.get("url")
        if not isinstance(name, str) or not isinstance(url, str):
            continue
        checksum = raw.get("sha256")
        assets.append(
            ReleaseAsset(
                name=name,
                url=url,
                sha256=checksum.strip().lower() if isinstance(checksum, str) else "",
            )
        )
    return assets


def release_from_payload(payload: dict[str, Any]) -> Release:
    version = payload.get("version") or payload.get("tag_name")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("更新清单缺少 version/tag_name")
    url = payload.get("url") or payload.get("html_url") or ""
    body = payload.get("body") or payload.get("release_summary") or payload.get("notes") or ""
    assets = _payload_assets(payload)
    return Release(
        version=version.strip(),
        url=url.strip() if isinstance(url, str) else "",
        body=body.strip() if isinstance(body, str) else "",
        asset=select_update_asset(assets),
    )


def select_update_asset(assets: list[ReleaseAsset]) -> ReleaseAsset | None:
    candidates: list[tuple[int, ReleaseAsset]] = []
    for asset in assets:
        lower = asset.name.lower()
        if not is_windows_installer_asset(lower):
            continue
        rank = 0 if "win64" in lower or "windows-x64" in lower or "win-x64" in lower else 1
        candidates.append((rank, asset))
    candidates.sort(key=lambda item: (item[0], item[1].name.lower()))
    return candidates[0][1] if candidates else None


def is_windows_installer_asset(name: str) -> bool:
    lower = name.lower()
    return (
        lower.endswith(".exe")
        and "hwt" in lower
        and "studio" in lower
        and ".sha256" not in lower
        and "source" not in lower
    )


def _request(url: str, *, accept: str = "application/json") -> urllib.request.Request:
    if not url.startswith(("https://", "http://")):
        raise ValueError(f"不支持的更新地址：{url}")
    return urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": f"HwtThemeStudio/{__version__}",
        },
    )


def _fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(_request(url), timeout=20) as response:
        payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("更新接口返回的不是 JSON 对象")
    return payload


def fetch_latest_release() -> Release:
    errors: list[str] = []
    for endpoint in (DEFAULT_LATEST_JSON_URL, DEFAULT_RELEASES_API_URL):
        try:
            return release_from_payload(_fetch_json(endpoint))
        except Exception as exc:  # pragma: no cover - network failures vary by machine
            errors.append(f"{endpoint}: {exc}")
    raise RuntimeError("无法读取 GitHub Release 更新信息：" + "；".join(errors))


def check_for_update(current_version: str = __version__) -> UpdateCheck:
    release = fetch_latest_release()
    available = is_newer_version(release.version, current_version)
    return UpdateCheck(
        current_version=current_version,
        latest_version=release.version,
        release=release,
        update_available=available,
    )


def safe_asset_name(name: str) -> str:
    if not name.strip() or any(token in name for token in ("/", "\\", ":", "\x00")):
        raise ValueError(f"非法更新文件名：{name}")
    if name in {".", ".."}:
        raise ValueError(f"非法更新文件名：{name}")
    return name


def _valid_sha256(value: str) -> str:
    normalized = value.strip().lower()
    return normalized if re.fullmatch(r"[0-9a-f]{64}", normalized) else ""


def _extract_sha256(text: str) -> str:
    match = re.search(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", text.lower())
    return match.group(0) if match else ""


def _fetch_checksum(url: str) -> str:
    try:
        with urllib.request.urlopen(_request(url, accept="text/plain"), timeout=20) as response:
            return _extract_sha256(response.read(4096).decode("utf-8", errors="replace"))
    except (OSError, ValueError, urllib.error.URLError):
        return ""


def _asset_checksum(asset: ReleaseAsset) -> str:
    checksum = _valid_sha256(asset.sha256)
    if checksum:
        return checksum
    return _fetch_checksum(asset.url + ".sha256")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_asset(
    release: Release,
    download_dir: Path | None = None,
    *,
    progress: ProgressCallback | None = None,
) -> Path:
    asset = release.asset
    if asset is None:
        raise ValueError("该 Release 没有适用于 Windows 的桌面安装包")
    expected = _asset_checksum(asset)
    if not expected:
        raise ValueError("发布包缺少有效 SHA-256 校验值，已拒绝自动更新")

    target_dir = download_dir or data_dir() / "updates"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_asset_name(asset.name)
    if target.is_file() and _sha256(target) == expected:
        if progress:
            progress(target.stat().st_size, target.stat().st_size, "已复用已校验的更新包")
        return target

    partial = target.with_name(target.name + ".part")
    partial.unlink(missing_ok=True)
    digest = hashlib.sha256()
    received = 0
    try:
        with urllib.request.urlopen(_request(asset.url, accept="application/octet-stream"), timeout=60) as response:
            raw_total = response.headers.get("Content-Length", "")
            total = int(raw_total) if raw_total.isdigit() else 0
            if total > MAX_DOWNLOAD_SIZE:
                raise ValueError("更新包超过允许的大小限制")
            with partial.open("wb") as handle:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    received += len(block)
                    if received > MAX_DOWNLOAD_SIZE:
                        raise ValueError("更新包超过允许的大小限制")
                    digest.update(block)
                    handle.write(block)
                    if progress:
                        progress(received, total, "正在下载更新包…")
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    actual = digest.hexdigest()
    if actual != expected:
        partial.unlink(missing_ok=True)
        raise ValueError(f"更新包 SHA-256 校验失败：期望 {expected}，实际 {actual}")
    os.replace(partial, target)
    if progress:
        progress(received, total or received, "更新包校验完成")
    return target


def release_page_url(release: Release) -> str:
    return release.url or f"https://github.com/{DEFAULT_REPOSITORY}/releases/tag/{release.version}"


def launch_update(downloaded_path: Path) -> bool:
    """Start a verified update; return True when the current process should exit."""
    downloaded_path = downloaded_path.resolve()
    if not downloaded_path.is_file():
        raise FileNotFoundError(downloaded_path)
    if os.name != "nt" or not getattr(sys, "frozen", False):
        subprocess.Popen([str(downloaded_path)])
        return False

    target = Path(sys.executable).resolve()
    if target == downloaded_path:
        return False
    script = f"""
$processId = {os.getpid()}
$source = '{str(downloaded_path).replace("'", "''")}'
$target = '{str(target).replace("'", "''")}'
$backup = $target + '.previous'
$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline -and (Get-Process -Id $processId -ErrorAction SilentlyContinue)) {{
    Start-Sleep -Milliseconds 200
}}
if (Get-Process -Id $processId -ErrorAction SilentlyContinue) {{ exit 2 }}
try {{
    Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
    Copy-Item -LiteralPath $target -Destination $backup -Force
    Copy-Item -LiteralPath $source -Destination $target -Force
    Start-Process -FilePath $target
    Remove-Item -LiteralPath $source -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
}} catch {{
    if (Test-Path -LiteralPath $backup) {{
        Copy-Item -LiteralPath $backup -Destination $target -Force
    }}
    exit 3
}}
"""
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
        creationflags=creation_flags,
        close_fds=True,
    )
    return True
