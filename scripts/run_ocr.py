"""Run PP-StructureV3 in a child process and persist its Markdown result.

The parent worker applies a timeout to this program, so a problematic scan cannot
hold a Cloud Tasks worker indefinitely. This file is intentionally tiny because
it runs once per scanned page.
"""
from __future__ import annotations

import sys
from pathlib import Path

from paddleocr import PPStructureV3


def main(input_path: str, output_path: str) -> None:
    output_dir = Path(output_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    pipeline = PPStructureV3(use_table_recognition=True)
    results = pipeline.predict(input=input_path)
    count = 0
    for result in results:
        result.save_to_markdown(save_path=str(output_dir))
        count += 1
    if count == 0:
        raise RuntimeError("PP-StructureV3 returned no page result")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: run_ocr.py INPUT_IMAGE OUTPUT_DIRECTORY")
    main(sys.argv[1], sys.argv[2])
