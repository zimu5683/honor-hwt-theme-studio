from __future__ import annotations

import hashlib
import re
import shlex
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


def transfer_to_phone(path: Path, host: str = "phone-termux", timeout: int = 1800) -> dict:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    filename = re.sub(r"[\s/\\]+", "_", path.name)
    remote_final = f"{REMOTE_DIR}/{filename}"
    remote_temp = remote_final + ".uploading"
    digest = local_sha256(path)

    probe = _run(["ssh", host, "printf ready"], timeout=30)
    if probe.returncode != 0 or "ready" not in probe.stdout:
        raise RuntimeError("无法连接 phone-termux：" + (probe.stderr or probe.stdout).strip())

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
        ["ssh", host, f"mv -f {shlex.quote(remote_temp)} {shlex.quote(remote_final)}"],
        timeout=60,
    )
    if finalize.returncode != 0:
        raise RuntimeError("手机端改名失败：" + (finalize.stderr or finalize.stdout).strip())
    return {"local": str(path), "remote": remote_final, "sha256": digest}
