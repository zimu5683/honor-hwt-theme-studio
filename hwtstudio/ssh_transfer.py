from __future__ import annotations

import hashlib
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .phone_transfer import TransferCancelled, safe_hwt_filename


REMOTE_DIR = "/storage/emulated/0/Honor/Themes"


def _run(args: list[str], *, timeout: int, check: bool = False,
         cancelled: threading.Event | None = None) -> subprocess.CompletedProcess:
    """Run OpenSSH with UTF-8 decoding (Windows' default GBK breaks Chinese paths)."""
    if cancelled is None:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=check,
        )
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    deadline = time.monotonic() + timeout
    while process.poll() is None:
        if cancelled.is_set():
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            process.communicate()
            raise TransferCancelled()
        if time.monotonic() >= deadline:
            process.kill()
            stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(args, timeout, output=stdout, stderr=stderr)
        time.sleep(0.05)
    stdout, stderr = process.communicate()
    result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    if check and result.returncode:
        raise subprocess.CalledProcessError(result.returncode, args, output=stdout, stderr=stderr)
    return result


def _run_with_cancel(args: list[str], *, timeout: int, check: bool = False,
                     cancelled: threading.Event | None = None) -> subprocess.CompletedProcess:
    if cancelled is None:
        return _run(args, timeout=timeout, check=check)
    return _run(args, timeout=timeout, check=check, cancelled=cancelled)

def local_sha256(path: Path, *, cancelled: threading.Event | None = None) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            if cancelled and cancelled.is_set():
                raise TransferCancelled()
            digest.update(block)
    return digest.hexdigest()


def _file_signature(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _ensure_file_signature(path: Path, expected: tuple[int, int, int, int], stage: str) -> None:
    try:
        current = _file_signature(path)
    except OSError as exc:
        raise RuntimeError(f"主题文件在{stage}时不可用，请重新选择文件") from exc
    if current != expected:
        raise RuntimeError(f"主题文件在{stage}时发生变化，请重新选择文件")


def preflight_phone(host: str = "phone-termux", *, cancelled: threading.Event | None = None) -> dict:
    checks: list[dict] = []
    errors: list[str] = []
    warnings: list[str] = []
    for executable in ("ssh", "scp"):
        found = shutil.which(executable)
        checks.append({"name": executable, "ok": bool(found), "detail": found or "未找到"})
        if not found:
            errors.append(f"未找到 {executable}，请安装或启用 Windows OpenSSH 客户端")
    if errors:
        return {"valid": False, "checks": checks, "errors": errors, "warnings": warnings}

    probe = _run_with_cancel(["ssh", host, "printf ready"], timeout=30, cancelled=cancelled)
    connected = probe.returncode == 0 and "ready" in probe.stdout
    checks.append({"name": "连接", "ok": connected, "detail": (probe.stderr or probe.stdout).strip()})
    if not connected:
        errors.append("无法连接 phone-termux，请检查 SSH 主机别名、手机网络和 Termux sshd")
        return {"valid": False, "checks": checks, "errors": errors, "warnings": warnings}

    quoted_dir = shlex.quote(REMOTE_DIR)
    directory = _run_with_cancel(
        ["ssh", host, f"mkdir -p {quoted_dir} && test -w {quoted_dir}"], timeout=30, cancelled=cancelled
    )
    writable = directory.returncode == 0
    checks.append({"name": "主题目录可写", "ok": writable, "detail": (directory.stderr or directory.stdout).strip()})
    if not writable:
        errors.append(f"手机主题目录不可写：{REMOTE_DIR}")

    tools = _run_with_cancel(
        ["ssh", host, "command -v sha256sum >/dev/null && printf hash_ok; command -v am >/dev/null && printf ' am_ok'; command -v termux-media-scan >/dev/null && printf ' scan_ok'"],
        timeout=30,
        cancelled=cancelled,
    )
    has_hash = "hash_ok" in tools.stdout
    has_am = "am_ok" in tools.stdout
    has_media_scan = "scan_ok" in tools.stdout
    checks.append({"name": "SHA-256", "ok": has_hash, "detail": "sha256sum" if has_hash else "未找到 sha256sum"})
    checks.append({"name": "打开主题应用", "ok": has_am, "detail": "am" if has_am else "未找到 am"})
    checks.append({"name": "媒体索引", "ok": has_media_scan, "detail": "termux-media-scan" if has_media_scan else "未找到 termux-media-scan"})
    if not has_hash:
        errors.append("手机端缺少 sha256sum，无法验证上传完整性")
    if not has_am:
        warnings.append("手机端缺少 am，上传后需要手动打开荣耀主题应用")
    if not has_media_scan:
        warnings.append("手机端缺少 termux-media-scan，主题应用可能无法立即识别新主题")
    return {"valid": not errors, "checks": checks, "errors": errors, "warnings": warnings}


def transfer_to_phone(path: Path, host: str = "phone-termux", timeout: int = 1800,
                      *, cancelled: threading.Event | None = None) -> dict:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    if cancelled and cancelled.is_set():
        raise TransferCancelled()
    filename = safe_hwt_filename(path.name)
    remote_final = f"{REMOTE_DIR}/{filename}"
    remote_temp = f"{remote_final}.{uuid.uuid4().hex}.uploading"
    initial_signature = _file_signature(path)
    digest = local_sha256(path, cancelled=cancelled)
    _ensure_file_signature(path, initial_signature, "校验后")

    preflight = preflight_phone(host, cancelled=cancelled)
    if not preflight["valid"]:
        raise RuntimeError("手机连接预检失败：" + "；".join(preflight["errors"]))
    _ensure_file_signature(path, initial_signature, "发送前")

    remote_started = False
    finalized = False
    try:
        _run_with_cancel(
            ["ssh", host, f"rm -f {shlex.quote(remote_temp)}"],
            check=True,
            timeout=30,
            cancelled=cancelled,
        )
        remote_started = True
        upload = _run_with_cancel(
            ["scp", str(path), f"{host}:{remote_temp}"],
            timeout=timeout,
            cancelled=cancelled,
        )
        if upload.returncode != 0:
            raise RuntimeError("上传失败：" + (upload.stderr or upload.stdout).strip())
        check = _run_with_cancel(
            ["ssh", host, f"sha256sum {shlex.quote(remote_temp)}"],
            timeout=120,
            cancelled=cancelled,
        )
        remote_sha = check.stdout.strip().split()[0].lower() if check.returncode == 0 and check.stdout.strip() else ""
        if remote_sha != digest.lower():
            raise RuntimeError(f"上传校验失败：本机 {digest}，手机 {remote_sha or '无结果'}")
        finalize = _run_with_cancel(
            ["ssh", host, f"mv -f {shlex.quote(remote_temp)} {shlex.quote(remote_final)} && sync"],
            timeout=60,
            cancelled=cancelled,
        )
        if finalize.returncode != 0:
            raise RuntimeError("手机端改名失败：" + (finalize.stderr or finalize.stdout).strip())
        finalized = True
        # scp writes straight to the filesystem and bypasses MediaStore, which
        # Theme Manager relies on to discover local themes. Register the file
        # with the media library before opening the app so a fresh scan sees it.
        media_scanned = False
        scan = _run_with_cancel(
            ["ssh", host, f"termux-media-scan {shlex.quote(remote_final)}"],
            timeout=60,
            cancelled=cancelled,
        )
        media_scanned = scan.returncode == 0
        # Opening the app makes the normal, unprivileged workflow explicit and
        # gives Theme Manager a chance to rescan its local-theme directory.  Do
        # not force-stop it here: Termux is intentionally not granted that power.
        opened = _run_with_cancel(
            [
                "ssh",
                host,
                "am start -a android.intent.action.MAIN "
                "-c android.intent.category.LAUNCHER "
                "-n com.hihonor.android.thememanager/.PageActivity",
            ],
            timeout=30,
            cancelled=cancelled,
        )
        return {
            "local": str(path),
            "remote": remote_final,
            "sha256": digest,
            "theme_app_opened": opened.returncode == 0,
            "media_scanned": media_scanned,
            "preflight": preflight,
        }
    finally:
        if remote_started and not finalized:
            try:
                _run_with_cancel(
                    ["ssh", host, f"rm -f {shlex.quote(remote_temp)}"],
                    timeout=15,
                    # Cleanup is best-effort but must still run after the
                    # caller sets the cancellation flag.
                    cancelled=None,
                )
            except Exception:
                pass
