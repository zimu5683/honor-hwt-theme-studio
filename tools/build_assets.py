from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hwtstudio.blank import create_blank_theme  # noqa: E402
from hwtstudio.catalog import (  # noqa: E402
    save_catalog,
    save_source_compatibility_report,
    scan_theme,
    source_compatibility_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="从大雪主题生成编辑器资源目录和空白 HWT")
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "assets")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    catalog = scan_theme(args.source)
    catalog_path = args.output / "catalog_daxue.json"
    blank_path = args.output / "空白主题_子木.hwt"
    compatibility_report_path = args.output / "source_compatibility.report.json"
    save_catalog(catalog, catalog_path)
    save_source_compatibility_report(catalog, compatibility_report_path)
    create_blank_theme(blank_path)
    report = source_compatibility_report(catalog)
    print(json.dumps({
        "catalog": str(catalog_path),
        "blank": str(blank_path),
        "compatibility_report": str(compatibility_report_path),
        "stats": catalog.stats,
        "warnings": report["summary"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
