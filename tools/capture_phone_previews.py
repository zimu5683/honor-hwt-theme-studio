"""Capture reproducible phone preview scenes through an authorized ADB device.

The command stores raw screenshots and UIAutomator dumps outside the packaged
assets. It deliberately validates the connected device and UI before taps so a
different screen cannot be modified by a stale coordinate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree


class CaptureError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    serial: str
    model: str
    android_release: str
    magic_os: str
    width: int
    height: int
    density: int


@dataclass(frozen=True, slots=True)
class UiNode:
    text: str
    content_desc: str
    resource_id: str
    class_name: str
    package: str
    bounds: tuple[int, int, int, int]
    clickable: bool

    @property
    def label(self) -> str:
        return self.text or self.content_desc or self.resource_id

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.bounds
        return ((left + right) // 2, (top + bottom) // 2)


def _run(command: list[str], *, binary: bool = False, check: bool = True) -> bytes | str:
    result = subprocess.run(command, capture_output=True, check=False)
    if check and result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise CaptureError(f"命令失败（{result.returncode}）：{' '.join(command)}\n{detail}")
    if binary:
        return result.stdout
    return result.stdout.decode("utf-8", "replace")


def _adb(info: DeviceInfo | None, *args: str, binary: bool = False, check: bool = True) -> bytes | str:
    serial = info.serial if info else None
    command = [os.environ.get("ADB", "adb")]
    if serial:
        command.extend(("-s", serial))
    command.extend(args)
    return _run(command, binary=binary, check=check)


def _shell(info: DeviceInfo, *args: str, check: bool = True) -> str:
    return str(_adb(info, "shell", *args, check=check))


def _single_device() -> str:
    output = str(_run([os.environ.get("ADB", "adb"), "devices"]))
    devices = [line.split("\t", 1)[0] for line in output.splitlines() if "\tdevice" in line]
    if len(devices) != 1:
        raise CaptureError(f"需要恰好一个已授权 ADB 设备，当前找到：{devices or '无'}")
    return devices[0]


def _prop(info_serial: str, name: str) -> str:
    return str(_adb(DeviceInfo(info_serial, "", "", "", 0, 0, 0), "shell", "getprop", name)).strip()


def _dimension(info: DeviceInfo, command: str, fallback: int) -> int:
    output = _shell(info, command, check=False)
    match = re.search(r"(\d+)x(\d+)", output)
    if not match:
        return fallback
    return int(match.group(1)) if command == "wm size" else int(match.group(2))


def device_info() -> DeviceInfo:
    serial = _single_device()
    bootstrap = DeviceInfo(serial, "", "", "", 0, 0, 0)
    size = _shell(bootstrap, "wm", "size")
    match = re.search(r"(?:Physical|Override) size: (\d+)x(\d+)", size)
    if not match:
        raise CaptureError(f"无法读取手机分辨率：{size.strip()}")
    density = re.search(r"(?:Physical|Override) density: (\d+)", _shell(bootstrap, "wm", "density"))
    return DeviceInfo(
        serial=serial,
        model=_prop(serial, "ro.product.model"),
        android_release=_prop(serial, "ro.build.version.release"),
        magic_os=_prop(serial, "ro.build.version.magic"),
        width=int(match.group(1)),
        height=int(match.group(2)),
        density=int(density.group(1)) if density else 0,
    )


def foreground_packages(info: DeviceInfo) -> set[str]:
    """Read the focused/resumed package when UIAutomator has no root node."""
    dump = _shell(info, "dumpsys", "activity", "activities", check=False)
    focused = "\n".join(
        line for line in dump.splitlines()
        if "mFocusedApp" in line or "mResumedActivity" in line
    )
    return {
        match.group(1)
        for match in re.finditer(r"u\d+\s+([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)/", focused)
    }


def dump_ui(info: DeviceInfo, *, allow_empty: bool = False) -> tuple[str, list[UiNode]]:
    raw = str(_adb(info, "exec-out", "uiautomator", "dump", "/dev/tty"))
    if "<hierarchy" not in raw:
        # Android 16 devices can return a valid dump only when uiautomator
        # writes to a remote file. Keep that file outside the repository and
        # remove it immediately after reading it back over ADB.
        remote_path = "/sdcard/hwtstudio-window.xml"
        try:
            _adb(info, "shell", "uiautomator", "dump", remote_path, check=False)
            raw = str(_adb(info, "exec-out", "cat", remote_path, check=False))
        finally:
            _adb(info, "shell", "rm", "-f", remote_path, check=False)
    start = raw.find("<hierarchy")
    if start < 0:
        if allow_empty:
            return "", []
        raise CaptureError(f"UIAutomator 未返回 XML：{raw[:400]}")
    xml_text = raw[start:]
    end = xml_text.rfind("</hierarchy>")
    if end < 0:
        raise CaptureError(f"UIAutomator XML 不完整：{xml_text[:400]}")
    xml_text = xml_text[: end + len("</hierarchy>")]
    root = ElementTree.fromstring(xml_text)
    nodes: list[UiNode] = []
    for element in root.iter("node"):
        raw_bounds = element.attrib.get("bounds", "")
        values = [int(value) for value in re.findall(r"\d+", raw_bounds)]
        if len(values) != 4:
            continue
        nodes.append(UiNode(
            text=element.attrib.get("text", ""),
            content_desc=element.attrib.get("content-desc", ""),
            resource_id=element.attrib.get("resource-id", ""),
            class_name=element.attrib.get("class", ""),
            package=element.attrib.get("package", ""),
            bounds=tuple(values),
            clickable=element.attrib.get("clickable", "false") == "true",
        ))
    return xml_text, nodes


def find_node(nodes: Iterable[UiNode], *, text: str = "", resource_id: str = "", content_desc: str = "") -> UiNode:
    candidates = [node for node in nodes if (
        (text and node.text == text)
        or (resource_id and node.resource_id == resource_id)
        or (content_desc and node.content_desc == content_desc)
    )]
    if not candidates:
        wanted = text or resource_id or content_desc
        raise CaptureError(f"当前页面找不到可确认的控件：{wanted}")
    return candidates[0]


def tap_node(info: DeviceInfo, node: UiNode) -> None:
    x, y = node.center
    _shell(info, "input", "tap", str(x), str(y))


def tappable_for(node: UiNode, nodes: Iterable[UiNode]) -> UiNode:
    """Use the smallest confirmed clickable container when a label is passive."""
    if node.clickable:
        return node
    left, top, right, bottom = node.bounds
    candidates = [candidate for candidate in nodes if candidate.clickable and (
        candidate.bounds[0] <= left and candidate.bounds[1] <= top
        and candidate.bounds[2] >= right and candidate.bounds[3] >= bottom
    )]
    if not candidates:
        return node
    return min(candidates, key=lambda item: (item.bounds[2] - item.bounds[0]) * (item.bounds[3] - item.bounds[1]))


def wait_for_package(info: DeviceInfo, package: str, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _xml, nodes = dump_ui(info)
        if any(node.package == package for node in nodes):
            return
        time.sleep(0.25)
    raise CaptureError(f"页面未进入预期应用：{package}")


def launch(info: DeviceInfo, component: str, package: str) -> None:
    _shell(info, "am", "start", "-n", component)
    wait_for_package(info, package)


def capture(
    info: DeviceInfo,
    output: Path,
    scene: str,
    expected_packages: Iterable[str] = (),
    *,
    allow_empty_ui: bool = False,
) -> Path:
    xml, nodes = dump_ui(info, allow_empty=allow_empty_ui)
    packages = sorted({node.package for node in nodes if node.package})
    if not packages and allow_empty_ui:
        packages = sorted(foreground_packages(info))
    expected = set(expected_packages)
    if expected and not expected.intersection(packages):
        raise CaptureError(f"场景 {scene} 的前台应用不符合预期：{packages}")
    output.mkdir(parents=True, exist_ok=True)
    image_path = output / f"{scene}.png"
    xml_path = output / f"{scene}.xml"
    metadata_path = output / f"{scene}.json"
    image_path.write_bytes(bytes(_adb(info, "exec-out", "screencap", "-p", binary=True)))
    xml_path.write_text(xml, encoding="utf-8")
    metadata_path.write_text(json.dumps({
        "scene": scene,
        "captured_at": datetime.now(UTC).isoformat(),
        "device": asdict(info),
        "packages": packages,
        "node_count": len(nodes),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return image_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("info", "dump-ui", "capture-current", "home", "recent", "notifications", "quick-settings", "collapse", "launch", "tap"))
    parser.add_argument("--output", type=Path, default=Path("phone-preview-raw"))
    parser.add_argument("--scene")
    parser.add_argument("--component")
    parser.add_argument("--package")
    parser.add_argument("--text")
    parser.add_argument("--resource-id")
    parser.add_argument("--content-desc")
    parser.add_argument("--allow-empty-ui", action="store_true", help="允许 UIAutomator 空根节点，并改用前台 Activity 校验")
    args = parser.parse_args()

    info = device_info()
    if args.command == "info":
        print(json.dumps(asdict(info), ensure_ascii=False, indent=2))
    elif args.command == "dump-ui":
        xml, nodes = dump_ui(info, allow_empty=args.allow_empty_ui)
        print(json.dumps({"node_count": len(nodes), "packages": sorted({n.package for n in nodes})}, ensure_ascii=False))
        if args.output:
            args.output.mkdir(parents=True, exist_ok=True)
            (args.output / "current.xml").write_text(xml, encoding="utf-8")
    elif args.command == "capture-current":
        if not args.scene:
            parser.error("capture-current 需要 --scene")
        print(capture(info, args.output, args.scene, allow_empty_ui=args.allow_empty_ui))
    elif args.command == "home":
        _shell(info, "input", "keyevent", "KEYCODE_HOME")
    elif args.command == "recent":
        _shell(info, "input", "keyevent", "KEYCODE_APP_SWITCH")
    elif args.command == "notifications":
        _shell(info, "cmd", "statusbar", "expand-notifications")
    elif args.command == "quick-settings":
        _shell(info, "cmd", "statusbar", "expand-settings")
    elif args.command == "collapse":
        _shell(info, "cmd", "statusbar", "collapse")
    elif args.command == "launch":
        if not args.component or not args.package:
            parser.error("launch 需要 --component 和 --package")
        launch(info, args.component, args.package)
    elif args.command == "tap":
        if not any((args.text, args.resource_id, args.content_desc)):
            parser.error("tap 需要 --text、--resource-id 或 --content-desc")
        _xml, nodes = dump_ui(info)
        node = find_node(nodes, text=args.text or "", resource_id=args.resource_id or "", content_desc=args.content_desc or "")
        node = tappable_for(node, nodes)
        tap_node(info, node)
        print(json.dumps({"label": node.label, "bounds": node.bounds}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
