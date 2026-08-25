"""Small JSON bridge for document engines isolated from the application venv.

This file deliberately imports no Agentic DS modules. Marker and MinerU have
dependency pins that conflict with the main ML runtime, so their Python
environments execute as replaceable workers and return only normalized bridge
data over stdout.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
from typing import Any


def _marker(source: Path, output_dir: Path) -> dict[str, Any]:
    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict
    from marker.output import text_from_rendered

    converter = PdfConverter(artifact_dict=create_model_dict())
    rendered = converter(str(source))
    markdown, metadata, images = text_from_rendered(rendered)
    image_dir = output_dir / source.stem
    image_dir.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, str]] = []
    for name, image in (images or {}).items():
        target = image_dir / Path(str(name)).name
        image.save(target)
        saved.append({"name": str(name), "path": str(target)})
    return {
        "engine_version": importlib.metadata.version("marker-pdf"),
        "markdown": str(markdown or ""),
        "metadata": metadata if isinstance(metadata, dict) else {},
        "images": saved,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("engine", choices=["marker"])
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    result = _marker(args.source, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
