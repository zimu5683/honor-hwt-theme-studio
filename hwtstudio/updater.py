from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable
import zipfile

from . import __version__
from . import bspatch
from .paths import APP_NAME, data_dir, ensure_no_symlink_parents


DEFAULT_REPOSITORY = "zimu5683/honor-hwt-theme-studio"
DEFAULT_LATEST_JSON_URL = (
    "https://github.com/zimu5683/honor-hwt-theme-studio/releases/latest/download/latest.json"
)
DEFAULT_RELEASES_API_URL = f"https://api.github.com/repos/{DEFAULT_REPOSITORY}/releases/latest"
# GitHub 直连在国内经常不可达，检查更新与下载都依次尝试：直连 → 国内加速镜像。
GITHUB_MIRROR_PREFIXES = (
    "https://ghfast.top/https://github.com/",
    "https://gh-proxy.com/https://github.com/",
)
MAX_DOWNLOAD_SIZE = 512 * 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_ASSET_NAME_BYTES = 200
MAX_RELEASE_VERSION_CHARS = 64
MAX_RELEASE_BODY_CHARS = 12_000
MAX_ARCHIVE_MEMBERS = 10_000
MAX_EXTRACTED_SIZE = 1 * 1024 * 1024 * 1024
PORTABLE_EXECUTABLE_NAME = f"{APP_NAME}.exe"
ProgressCallback = Callable[[int, int, str], None]


def _check_cancelled(cancelled: threading.Event | None):
    if cancelled and cancelled.is_set():
        raise RuntimeError("更新任务已取消")


def _is_portable_runtime() -> bool:
    """判断当前是否以「便携版」方式运行（即从 APP_NAME 目录启动）。"""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return False
    current = Path(sys.executable).resolve()
    return current.name == PORTABLE_EXECUTABLE_NAME and current.parent.name == APP_NAME


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    sha256: str = ""


@dataclass(frozen=True)
class PatchAsset:
    name: str
    url: str
    sha256: str = ""
    from_sha256: str = ""
    target_sha256: str = ""


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    body: str
    asset: ReleaseAsset | None
    patches: tuple[PatchAsset, ...] = ()


@dataclass(frozen=True)
class UpdateCheck:
    current_version: str
    latest_version: str | None
    release: Release | None
    update_available: bool


@dataclass(frozen=True)
class VerifiedDownload:
    path: Path
    sha256: str


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


def release_from_payload(payload: dict[str, Any], *, portable: bool = False) -> Release:
    version = payload.get("version") or payload.get("tag_name")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("更新清单缺少 version/tag_name")
    version = version.strip()
    if len(version) > MAX_RELEASE_VERSION_CHARS:
        raise ValueError("更新清单版本号过长")
    url = payload.get("url") or payload.get("html_url") or ""
    body = payload.get("body") or payload.get("release_summary") or payload.get("notes") or ""
    body = body.strip() if isinstance(body, str) else ""
    if len(body) > MAX_RELEASE_BODY_CHARS:
        body = body[:MAX_RELEASE_BODY_CHARS].rstrip() + "\n\n（更新说明过长，已截断）"
    assets = _payload_assets(payload)
    return Release(
        version=version,
        url=url.strip() if isinstance(url, str) else "",
        body=body,
        asset=select_update_asset(assets, portable=portable),
        patches=_payload_patches(payload),
    )


def _payload_patches(payload: dict[str, Any]) -> tuple[PatchAsset, ...]:
    """解析 latest.json 里的差分补丁清单，过滤掉不完整或不受信任的条目。"""
    raw_patches = payload.get("patches")
    if not isinstance(raw_patches, list):
        return ()
    patches: list[PatchAsset] = []
    for raw in raw_patches:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        url = raw.get("url") or raw.get("browser_download_url")
        sha256 = raw.get("sha256")
        from_sha256 = raw.get("from_sha256")
        target_sha256 = raw.get("target_sha256")
        if (
            not isinstance(name, str)
            or not isinstance(url, str)
            or not url.startswith("https://")
            or not all(
                isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value.strip().lower())
                for value in (sha256, from_sha256, target_sha256)
            )
        ):
            continue
        patches.append(
            PatchAsset(
                name=name,
                url=url,
                sha256=sha256.strip().lower(),
                from_sha256=from_sha256.strip().lower(),
                target_sha256=target_sha256.strip().lower(),
            )
        )
    return tuple(patches)


def select_update_asset(assets: list[ReleaseAsset], *, portable: bool = False) -> ReleaseAsset | None:
    candidates: list[tuple[int, ReleaseAsset]] = []
    for asset in assets:
        lower = asset.name.lower()
        if not is_windows_installer_asset(lower):
            continue
        if is_windows_setup_asset(lower):
            # 便携版优先 ZIP（可走差分更新），安装版优先 Setup 安装器。
            package_rank = 1 if portable else 0
        elif lower.endswith(".zip"):
            package_rank = 0 if portable else 1
        else:
            package_rank = 2
        architecture_rank = 0 if "win64" in lower or "windows-x64" in lower or "win-x64" in lower else 1
        rank = package_rank * 2 + architecture_rank
        candidates.append((rank, asset))
    candidates.sort(key=lambda item: (item[0], item[1].name.lower()))
    return candidates[0][1] if candidates else None


def is_windows_installer_asset(name: str) -> bool:
    lower = name.lower()
    return (
        lower.endswith((".exe", ".zip"))
        and "hwt" in lower
        and "studio" in lower
        and ".sha256" not in lower
        and "source" not in lower
    )


def is_windows_setup_asset(name: str) -> bool:
    lower = name.lower()
    return lower.endswith(".exe") and lower.removesuffix(".exe").endswith(("-setup", "_setup"))


def _request(url: str, *, accept: str = "application/json") -> urllib.request.Request:
    if not url.startswith("https://"):
        raise ValueError(f"更新地址必须使用 HTTPS：{url}")
    return urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": f"HwtThemeStudio/{__version__}",
        },
    )


def _read_metadata(response, *, limit: int, context: str) -> bytes:
    raw_length = response.headers.get("Content-Length", "")
    declared_length: int | None = None
    if raw_length:
        if not raw_length.isdigit():
            raise ValueError(f"{context}响应长度无效")
        declared_length = int(raw_length)
        if declared_length > limit:
            raise ValueError(f"{context}响应过大")
    body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError(f"{context}响应过大")
    if declared_length is not None and len(body) != declared_length:
        raise ValueError(f"{context}响应长度与声明不一致")
    return body


def _github_url_candidates(url: str) -> list[str]:
    """返回 [直连, 镜像1, 镜像2...]，镜像只对 github.com 的 URL 生效。"""
    if url.startswith("https://github.com/"):
        return [url] + [f"{prefix}{url}" for prefix in GITHUB_MIRROR_PREFIXES]
    return [url]


def _fetch_json(url: str, *, cancelled: threading.Event | None = None) -> dict[str, Any]:
    candidates = _github_url_candidates(url)
    last_error: Exception | None = None
    for candidate in candidates:
        _check_cancelled(cancelled)
        try:
            with urllib.request.urlopen(_request(candidate), timeout=20) as response:
                payload = json.loads(
                    _read_metadata(response, limit=MAX_METADATA_BYTES, context="更新清单").decode("utf-8")
                )
            _check_cancelled(cancelled)
            if not isinstance(payload, dict):
                raise ValueError("更新接口返回的不是 JSON 对象")
            return payload
        except Exception as exc:
            last_error = exc
    if last_error is None:
        raise RuntimeError("无法读取更新清单")
    if len(candidates) == 1:
        # 没有镜像可回退时保持原始异常类型，与旧行为一致。
        raise last_error
    raise RuntimeError(f"无法读取更新清单：{last_error}") from last_error


def fetch_latest_release(*, cancelled: threading.Event | None = None, portable: bool | None = None) -> Release:
    portable = _is_portable_runtime() if portable is None else portable
    errors: list[str] = []
    for endpoint in (DEFAULT_LATEST_JSON_URL, DEFAULT_RELEASES_API_URL):
        try:
            return release_from_payload(_fetch_json(endpoint, cancelled=cancelled), portable=portable)
        except RuntimeError:
            raise
        except Exception as exc:  # pragma: no cover - network failures vary by machine
            errors.append(f"{endpoint}: {exc}")
    raise RuntimeError("无法读取 GitHub Release 更新信息：" + "；".join(errors))


def check_for_update(current_version: str = __version__, *, cancelled: threading.Event | None = None) -> UpdateCheck:
    release = fetch_latest_release(cancelled=cancelled)
    _check_cancelled(cancelled)
    available = is_newer_version(release.version, current_version)
    return UpdateCheck(
        current_version=current_version,
        latest_version=release.version,
        release=release,
        update_available=available,
    )


def safe_asset_name(name: str) -> str:
    if (
        not isinstance(name, str)
        or not name.strip()
        or name != name.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
        or any(token in name for token in ("/", "\\", ":", "\x00"))
    ):
        raise ValueError(f"非法更新文件名：{name}")
    if name in {".", ".."}:
        raise ValueError(f"非法更新文件名：{name}")
    if len(name.encode("utf-8")) > MAX_ASSET_NAME_BYTES:
        raise ValueError("更新文件名过长")
    return name


def _valid_sha256(value: str) -> str:
    normalized = value.strip().lower()
    return normalized if re.fullmatch(r"[0-9a-f]{64}", normalized) else ""


def _extract_sha256(text: str) -> str:
    match = re.search(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", text.lower())
    return match.group(0) if match else ""


def _fetch_checksum(url: str, *, cancelled: threading.Event | None = None) -> str:
    for candidate in _github_url_candidates(url):
        try:
            _check_cancelled(cancelled)
            with urllib.request.urlopen(_request(candidate, accept="text/plain"), timeout=20) as response:
                value = _extract_sha256(
                    _read_metadata(response, limit=4096, context="校验文件").decode("utf-8", errors="replace")
                )
            _check_cancelled(cancelled)
            return value
        except RuntimeError:
            raise
        except (OSError, ValueError, urllib.error.URLError):
            continue
    return ""


def _asset_checksum(asset: ReleaseAsset, *, cancelled: threading.Event | None = None) -> str:
    _check_cancelled(cancelled)
    checksum = _valid_sha256(asset.sha256)
    if checksum:
        return checksum
    return _fetch_checksum(asset.url + ".sha256", cancelled=cancelled)


def _sha256(path: Path, *, cancelled: threading.Event | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            _check_cancelled(cancelled)
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _find_patch_and_base(
    release: Release,
    expected: str,
    target_dir: Path,
    cancelled: threading.Event | None,
) -> tuple[PatchAsset | None, Path | None]:
    """在缓存目录里找到能生成 ``expected`` 的补丁及其旧包 base。

    只有便携 ZIP 资产（且补丁 target 匹配）才会命中；否则返回 (None, None)，
    由调用方回退到全量下载。
    """
    asset = release.asset
    if asset is None or not asset.name.lower().endswith(".zip") or not release.patches:
        return None, None
    for patch in release.patches:
        if patch.target_sha256 != expected:
            continue
        _check_cancelled(cancelled)
        for entry in target_dir.iterdir():
            if entry.is_symlink() or not entry.is_file():
                continue
            try:
                if _sha256(entry, cancelled=cancelled) == patch.from_sha256:
                    return patch, entry
            except OSError:
                continue
    return None, None


def _download_and_apply_patch(
    release: Release,
    patch: PatchAsset,
    base: Path,
    target: Path,
    target_dir: Path,
    expected: str,
    progress: ProgressCallback | None,
    cancelled: threading.Event | None,
) -> VerifiedDownload:
    """下载差分补丁，用缓存的旧包还原出完整新版并校验。"""
    patch_partial = target_dir / f".{patch.name}.{os.getpid()}.{threading.get_ident()}.part"
    patch_partial.unlink(missing_ok=True)
    last_error: Exception | None = None
    for candidate in _github_url_candidates(patch.url):
        _check_cancelled(cancelled)
        patch_partial.unlink(missing_ok=True)
        try:
            if progress:
                progress(0, 0, "正在下载差分补丁…")
            with urllib.request.urlopen(_request(candidate, accept="application/octet-stream"), timeout=60) as response:
                with patch_partial.open("wb") as handle:
                    while True:
                        _check_cancelled(cancelled)
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        handle.write(block)
            break
        except RuntimeError:
            raise  # 取消任务必须立刻中止，不能回退到镜像
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    new_partial = target_dir / f".{target.name}.{os.getpid()}.{threading.get_ident()}.new"
    try:
        _check_cancelled(cancelled)
        if _sha256(patch_partial, cancelled=cancelled) != patch.sha256:
            raise ValueError("补丁 SHA-256 校验失败，已拒绝差分更新")
        if progress:
            progress(0, 0, "正在应用差分补丁…")
        bspatch.apply_file(base, patch_partial, new_partial)
        _check_cancelled(cancelled)
        if _sha256(new_partial, cancelled=cancelled) != expected:
            raise ValueError("还原后的文件 SHA-256 校验失败，已拒绝差分更新")
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError("更新包缓存目标已变为非普通文件")
        os.replace(new_partial, target)
    except Exception:
        new_partial.unlink(missing_ok=True)
        raise
    finally:
        patch_partial.unlink(missing_ok=True)
    if progress:
        progress(target.stat().st_size, target.stat().st_size, "差分更新包已就绪")
    return VerifiedDownload(path=target, sha256=expected)


def download_asset(
    release: Release,
    download_dir: Path | None = None,
    *,
    progress: ProgressCallback | None = None,
    cancelled: threading.Event | None = None,
) -> VerifiedDownload:
    asset = release.asset
    if asset is None:
        raise ValueError("该 Release 没有适用于 Windows 的桌面安装包")
    expected = _asset_checksum(asset, cancelled=cancelled)
    if not expected:
        raise ValueError("发布包缺少有效 SHA-256 校验值，已拒绝自动更新")

    target_dir = Path(download_dir) if download_dir is not None else data_dir() / "updates"
    _check_cancelled(cancelled)
    if target_dir.is_symlink():
        raise ValueError("更新包缓存目录不能是符号链接")
    if target_dir.exists() and not target_dir.is_dir():
        raise ValueError("更新包缓存目录不是目录")
    ensure_no_symlink_parents(target_dir / ".hwtstudio-path-check", "更新包缓存目录的父路径不能包含符号链接")
    target_dir.mkdir(parents=True, exist_ok=True)
    ensure_no_symlink_parents(target_dir / ".hwtstudio-path-check", "更新包缓存目录的父路径不能包含符号链接")
    if target_dir.is_symlink() or not target_dir.is_dir():
        raise ValueError("更新包缓存目录不是普通目录")
    target = target_dir / safe_asset_name(asset.name)
    if target.is_symlink():
        raise ValueError("更新包缓存不能是符号链接")
    if target.exists() and not target.is_file():
        raise ValueError("更新包缓存不是普通文件")
    if target.is_file() and _sha256(target, cancelled=cancelled) == expected:
        if progress:
            progress(target.stat().st_size, target.stat().st_size, "已复用已校验的更新包")
        return VerifiedDownload(path=target, sha256=expected)

    # 差分更新：缓存里存在与补丁 from_sha256 匹配的旧包时，只下载小补丁。
    patch, base = _find_patch_and_base(release, expected, target_dir, cancelled)
    if patch is not None and base is not None:
        return _download_and_apply_patch(
            release, patch, base, target, target_dir, expected, progress, cancelled
        )

    partial = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}.part")
    if partial.is_symlink():
        raise ValueError("更新包临时文件不能是符号链接")
    if partial.exists() and not partial.is_file():
        raise ValueError("更新包临时文件不是普通文件")
    partial.unlink(missing_ok=True)
    digest = hashlib.sha256()
    received = 0
    total = 0
    last_error: Exception | None = None
    for candidate in _github_url_candidates(asset.url):
        _check_cancelled(cancelled)
        partial.unlink(missing_ok=True)
        digest = hashlib.sha256()
        received = 0
        total = 0
        try:
            with urllib.request.urlopen(_request(candidate, accept="application/octet-stream"), timeout=60) as response:
                raw_total = response.headers.get("Content-Length", "")
                declared_total: int | None = None
                if raw_total:
                    if not raw_total.isdigit():
                        raise ValueError("更新包响应长度无效")
                    declared_total = int(raw_total)
                total = declared_total or 0
                if declared_total is not None and declared_total > MAX_DOWNLOAD_SIZE:
                    raise ValueError("更新包超过允许的大小限制")
                with partial.open("wb") as handle:
                    while True:
                        _check_cancelled(cancelled)
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
                if declared_total is not None and received != declared_total:
                    raise ValueError("更新包响应长度与声明不一致")
            break
        except RuntimeError:
            raise  # 取消任务必须立刻中止，不能回退到镜像
        except Exception as exc:
            last_error = exc
            partial.unlink(missing_ok=True)
    if last_error is not None:
        raise last_error
    actual = digest.hexdigest()
    try:
        _check_cancelled(cancelled)
        if actual != expected:
            raise ValueError(f"更新包 SHA-256 校验失败：期望 {expected}，实际 {actual}")
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError("更新包缓存目标已变为非普通文件")
        os.replace(partial, target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    if progress:
        progress(received, total or received, "更新包校验完成")
    return VerifiedDownload(path=target, sha256=expected)


def _archive_member_path(info: zipfile.ZipInfo) -> tuple[PurePosixPath, bool]:
    raw_name = info.filename.replace("\\", "/")
    is_directory = raw_name.endswith("/")
    normalized = raw_name.rstrip("/")
    if not normalized or "\x00" in normalized:
        raise ValueError("更新 ZIP 含有非法条目路径")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(":" in part for part in path.parts)
    ):
        raise ValueError("更新 ZIP 含有路径穿越条目")
    mode = (info.external_attr >> 16) & 0o170000
    if mode == stat.S_IFLNK:
        raise ValueError("更新 ZIP 不允许符号链接条目")
    return path, is_directory


def _extract_portable_archive(archive: Path) -> tuple[Path, Path]:
    """Extract a verified portable package into a private staging directory."""
    staging_root = Path(tempfile.mkdtemp(prefix=".hwt-update-", dir=str(archive.parent)))
    try:
        seen: dict[PurePosixPath, bool] = {}
        total_size = 0
        with zipfile.ZipFile(archive) as bundle:
            infos = bundle.infolist()
            if len(infos) > MAX_ARCHIVE_MEMBERS:
                raise ValueError("更新 ZIP 条目数量超过限制")
            parsed: list[tuple[zipfile.ZipInfo, PurePosixPath, bool]] = []
            for info in infos:
                path, is_directory = _archive_member_path(info)
                if path in seen:
                    raise ValueError("更新 ZIP 含有重复条目")
                for parent in path.parents:
                    if parent in seen and not seen[parent]:
                        raise ValueError("更新 ZIP 含有文件/目录路径冲突")
                seen[path] = is_directory
                if not is_directory:
                    total_size += info.file_size
                    if total_size > MAX_EXTRACTED_SIZE:
                        raise ValueError("更新 ZIP 解压内容超过限制")
                parsed.append((info, path, is_directory))

            expected_executable = PurePosixPath(APP_NAME) / PORTABLE_EXECUTABLE_NAME
            if expected_executable not in seen or seen[expected_executable]:
                raise ValueError("更新 ZIP 缺少桌面程序")

            root = staging_root.resolve()
            for info, path, is_directory in parsed:
                destination = staging_root.joinpath(*path.parts)
                if not destination.resolve().is_relative_to(root):
                    raise ValueError("更新 ZIP 条目超出暂存目录")
                if is_directory:
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, destination.open("xb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)

        executable = staging_root / APP_NAME / PORTABLE_EXECUTABLE_NAME
        if executable.is_symlink() or not executable.is_file():
            raise ValueError("更新 ZIP 中的桌面程序不是普通文件")
        return executable.parent, staging_root
    except Exception:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise


def release_page_url(release: Release) -> str:
    return release.url or f"https://github.com/{DEFAULT_REPOSITORY}/releases/tag/{release.version}"


def _spawn_encoded_powershell(script: str) -> None:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
        creationflags=creation_flags,
        close_fds=True,
    )


def _launch_portable_update(
    staged_app: Path,
    staging_root: Path,
    target_dir: Path,
    source_archive: Path,
    expected: str,
) -> bool:
    source = staged_app.absolute()
    staging = staging_root.absolute()
    target = target_dir.absolute()
    archive = source_archive.absolute()
    executable = PORTABLE_EXECUTABLE_NAME.replace("'", "''")
    script = f"""
$processId = {os.getpid()}
$source = '{str(source).replace("'", "''")}'
$staging = '{str(staging).replace("'", "''")}'
$target = '{str(target).replace("'", "''")}'
$archive = '{str(archive).replace("'", "''")}'
$expected = '{expected}'
$backup = $target + '.previous'
$deadline = (Get-Date).AddSeconds(30)
function Get-Sha256([string] $path) {{
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
}}
function Assert-PlainPath([string] $path) {{
    if (Test-Path -LiteralPath $path) {{
        $item = Get-Item -LiteralPath $path -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {{
            throw '更新目录不能是符号链接或重解析点'
        }}
    }}
}}
while ((Get-Date) -lt $deadline -and (Get-Process -Id $processId -ErrorAction SilentlyContinue)) {{
    Start-Sleep -Milliseconds 200
}}
if (Get-Process -Id $processId -ErrorAction SilentlyContinue) {{ exit 2 }}
$moved = $false
try {{
    Assert-PlainPath $archive
    Assert-PlainPath $source
    Assert-PlainPath $target
    if ((Get-Sha256 $archive) -ne $expected) {{ throw 'source checksum mismatch' }}
    if (-not (Test-Path -LiteralPath (Join-Path $source '{executable}') -PathType Leaf)) {{ throw 'portable executable missing' }}
    Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $target) {{ Move-Item -LiteralPath $target -Destination $backup -Force }}
    Move-Item -LiteralPath $source -Destination $target -Force
    $moved = $true
    Start-Process -FilePath (Join-Path $target '{executable}')
    Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
}} catch {{
    if ($moved -and (Test-Path -LiteralPath $target)) {{ Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue }}
    if (Test-Path -LiteralPath $backup) {{ Move-Item -LiteralPath $backup -Destination $target -Force }}
    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    exit 3
}}
"""
    _spawn_encoded_powershell(script)
    return True


def launch_update(download: VerifiedDownload) -> bool:
    """Start a verified update; return True when the current process should exit."""
    expected = _valid_sha256(download.sha256)
    if not expected:
        raise ValueError("更新包缺少有效 SHA-256 校验值，已拒绝启动")
    downloaded_path = Path(download.path)
    if downloaded_path.is_symlink():
        raise ValueError("更新包不能是符号链接")
    if not downloaded_path.is_file():
        raise FileNotFoundError(downloaded_path)
    downloaded_path = downloaded_path.absolute()
    actual = _sha256(downloaded_path)
    if actual != expected:
        raise ValueError(f"启动前更新包 SHA-256 校验失败：期望 {expected}，实际 {actual}")

    if is_windows_setup_asset(downloaded_path.name):
        subprocess.Popen([str(downloaded_path)])
        return os.name == "nt" and bool(getattr(sys, "frozen", False))

    if downloaded_path.suffix.lower() == ".zip":
        staged_app, staging_root = _extract_portable_archive(downloaded_path)
        if os.name != "nt" or not getattr(sys, "frozen", False):
            subprocess.Popen([str(staged_app / PORTABLE_EXECUTABLE_NAME)])
            return False
        current = Path(sys.executable).resolve()
        if current.name == PORTABLE_EXECUTABLE_NAME and current.parent.name == APP_NAME:
            target_dir = current.parent
        else:
            target_dir = current.parent / APP_NAME
        return _launch_portable_update(
            staged_app,
            staging_root,
            target_dir,
            downloaded_path,
            expected,
        )

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
$expected = '{expected}'
$staged = Join-Path ([System.IO.Path]::GetTempPath()) ("hwt-update-" + $processId + ".tmp")
$backup = $target + '.previous'
$deadline = (Get-Date).AddSeconds(30)
function Get-Sha256([string] $path) {{
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
}}
while ((Get-Date) -lt $deadline -and (Get-Process -Id $processId -ErrorAction SilentlyContinue)) {{
    Start-Sleep -Milliseconds 200
}}
if (Get-Process -Id $processId -ErrorAction SilentlyContinue) {{ exit 2 }}
try {{
    Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue
    if ((Get-Sha256 $source) -ne $expected) {{ throw 'source checksum mismatch' }}
    Copy-Item -LiteralPath $source -Destination $staged -Force
    if ((Get-Sha256 $staged) -ne $expected) {{ throw 'staged checksum mismatch' }}
    Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
    Copy-Item -LiteralPath $target -Destination $backup -Force
    Copy-Item -LiteralPath $staged -Destination $target -Force
    if ((Get-Sha256 $target) -ne $expected) {{ throw 'target checksum mismatch' }}
    Start-Process -FilePath $target
    Remove-Item -LiteralPath $source -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
}} catch {{
    if (Test-Path -LiteralPath $backup) {{
        Copy-Item -LiteralPath $backup -Destination $target -Force
    }}
    Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue
    exit 3
}}
"""
    _spawn_encoded_powershell(script)
    return True
