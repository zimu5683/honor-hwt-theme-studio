from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ResourceSlot:
    id: str
    module: str
    container: str
    resource_type: str
    name: str
    path: str
    category: str
    label: str
    status: str = "已验证"
    risk: str = "低"
    occurrences: int = 1
    width: int | None = None
    height: int | None = None
    mode: str | None = None
    actual_format: str | None = None
    extension: str | None = None
    ninepatch: bool = False
    png_chunks: dict[str, str] = field(default_factory=dict)
    synthetic: bool = False
    targets: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ResourceSlot:
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in value.items() if k in fields})


@dataclass(slots=True)
class ThemeCatalog:
    source_path: str
    source_sha256: str
    generated_at: str
    stats: dict[str, int]
    warnings: list[dict[str, Any]]
    resources: list[ResourceSlot]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "generated_at": self.generated_at,
            "stats": self.stats,
            "warnings": self.warnings,
            "resources": [item.to_dict() for item in self.resources],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ThemeCatalog:
        return cls(
            source_path=value.get("source_path", ""),
            source_sha256=value.get("source_sha256", ""),
            generated_at=value.get("generated_at", ""),
            stats=dict(value.get("stats", {})),
            warnings=list(value.get("warnings", [])),
            resources=[ResourceSlot.from_dict(x) for x in value.get("resources", [])],
        )


@dataclass(slots=True)
class ResourceChange:
    slot_id: str
    enabled: bool = True
    value: str | None = None
    source_file: str | None = None
    source_kind: str = "file"
    fit: str = "cover"
    focus_x: float = 0.5
    focus_y: float = 0.5
    enhance: str = "none"
    enhance_strength: float = 0.0
    # 常用应用页面背景的"话框/面板"处理方式:
    # "system" 跟随系统(不写入表面颜色)、"layered" 标题与页面全透明 +
    # 列表/卡片/按键半透明(编辑器新选择的默认值)、"frosted" 全部半透明磨砂、
    # "transparent" 全透明。只对合成背景槽位生效,其余槽位忽略。
    # 字段默认值保留 frosted，是为了兼容不含 surfaces 的旧工程。
    surfaces: str = "frosted"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ResourceChange:
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in value.items() if k in fields})


@dataclass
class ThemeProject:
    name: str = "我的主题"
    title: str = "空白主题"
    author: str = "子木"
    designer: str = "子木"
    version: str = "1.0.0"
    screen: str = "FHD"
    changes: dict[str, ResourceChange] = field(default_factory=dict)
    custom_resources: list[ResourceSlot] = field(default_factory=list)
    project_file: Path | None = None
    dirty: bool = False

    def set_change(self, change: ResourceChange) -> None:
        self.changes[change.slot_id] = change
        self.dirty = True

    def remove_change(self, slot_id: str) -> None:
        self.changes.pop(slot_id, None)
        self.dirty = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": 2,
            "name": self.name,
            "title": self.title,
            "author": self.author,
            "designer": self.designer,
            "version": self.version,
            "screen": self.screen,
            "changes": {k: v.to_dict() for k, v in self.changes.items()},
            "custom_resources": [x.to_dict() for x in self.custom_resources],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any], project_file: Path | None = None) -> ThemeProject:
        project = cls(
            name=value.get("name", "我的主题"),
            title=value.get("title", "空白主题"),
            author=value.get("author", "子木"),
            designer=value.get("designer", "子木"),
            version=value.get("version", "1.0.0"),
            screen=value.get("screen", "FHD"),
            changes={k: ResourceChange.from_dict(v) for k, v in value.get("changes", {}).items()},
            custom_resources=[ResourceSlot.from_dict(x) for x in value.get("custom_resources", [])],
            project_file=project_file,
            dirty=False,
        )
        return project
