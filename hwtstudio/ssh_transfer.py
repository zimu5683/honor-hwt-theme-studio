from __future__ import annotations

import hashlib
import re
import shlex
import shutil
import subprocess
from pathlib import Path


REMOTE_DIR = "/storage/emulated/0/Honor/Themes"


def _run(args: list[str], *, timeout: int, check: bool = False) -> subprocess.CompletedProcess:
    """Run OpenSSH with UTF-8 decoding (Windows' default GBK breaks Chinese paths)."""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=check,
    )


def local_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def preflight_phone(host: str = "phone-termux") -> dict:
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

    probe = _run(["ssh", host, "printf ready"], timeout=30)
    connected = probe.returncode == 0 and "ready" in probe.stdout
    checks.append({"name": "连接", "ok": connected, "detail": (probe.stderr or probe.stdout).strip()})
    if not connected:
        errors.append("无法连接 phone-termux，请检查 SSH 主机别名、手机网络和 Termux sshd")
        return {"valid": False, "checks": checks, "errors": errors, "warnings": warnings}

    quoted_dir = shlex.quote(REMOTE_DIR)
    directory = _run(["ssh", host, f"mkdir -p {quoted_dir} && test -w {quoted_dir}"], timeout=30)
    writable = directory.returncode == 0
    checks.append({"name": "主题目录可写", "ok": writable, "detail": (directory.stderr or directory.stdout).strip()})
    if not writable:
        errors.append(f"手机主题目录不可写：{REMOTE_DIR}")

    tools = _run(
        ["ssh", host, "command -v sha256sum >/dev/null && printf hash_ok; command -v am >/dev/null && printf ' am_ok'"],
        timeout=30,
    )
    has_hash = "hash_ok" in tools.stdout
    has_am = "am_ok" in tools.stdout
    checks.append({"name": "SHA-256", "ok": has_hash, "detail": "sha256sum" if has_hash else "未找到 sha256sum"})
    checks.append({"name": "打开主题应用", "ok": has_am, "detail": "am" if has_am else "未找到 am"})
    if not has_hash:
        errors.append("手机端缺少 sha256sum，无法验证上传完整性")
    if not has_am:
        warnings.append("手机端缺少 am，上传后需要手动打开荣耀主题应用")
    return {"valid": not errors, "checks": checks, "errors": errors, "warnings": warnings}


def transfer_to_phone(path: Path, host: str = "phone-termux", timeout: int = 1800) -> dict:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    filename = re.sub(r"[^\w.-]+", "_", path.name, flags=re.UNICODE).strip("._") or "theme.hwt"
    remote_final = f"{REMOTE_DIR}/{filename}"
    remote_temp = remote_final + ".uploading"
    digest = local_sha256(path)

    preflight = preflight_phone(host)
    if not preflight["valid"]:
        raise RuntimeError("手机连接预检失败：" + "；".join(preflight["errors"]))

    _run(
        ["ssh", host, f"rm -f {shlex.quote(remote_temp)}"],
        check=True,
        timeout=30,
    )
    upload = _run(
        ["scp", str(path), f"{host}:{remote_temp}"],
        timeout=timeout,
    )
    if upload.returncode != 0:
        raise RuntimeError("上传失败：" + (upload.stderr or upload.stdout).strip())
    check = _run(
        ["ssh", host, f"sha256sum {shlex.quote(remote_temp)}"],
        timeout=120,
    )
    remote_sha = check.stdout.strip().split()[0].lower() if check.returncode == 0 and check.stdout.strip() else ""
    if remote_sha != digest.lower():
        raise RuntimeError(f"上传校验失败：本机 {digest}，手机 {remote_sha or '无结果'}")
    finalize = _run(
        ["ssh", host, f"mv -f {shlex.quote(remote_temp)} {shlex.quote(remote_final)} && sync"],
        timeout=60,
    )
    if finalize.returncode != 0:
        raise RuntimeError("手机端改名失败：" + (finalize.stderr or finalize.stdout).strip())
    # Opening the app makes the normal, unprivileged workflow explicit and
    # gives Theme Manager a chance to rescan its local-theme directory.  Do
    # not force-stop it here: Termux is intentionally not granted that power.
    opened = _run(
        [
            "ssh",
            host,
            "am start -a android.intent.action.MAIN "
            "-c android.intent.category.LAUNCHER "
            "-n com.hihonor.android.thememanager/.PageActivity",
        ],
        timeout=30,
    )
    return {
        "local": str(path),
        "remote": remote_final,
        "sha256": digest,
        "theme_app_opened": opened.returncode == 0,
        "preflight": preflight,
    }
