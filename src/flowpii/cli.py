from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .excel_writer import write_inventory_excel
from .expand import expand_graph
from .models import BifGraph
from .recognize import recognize_file, recognize_from_fixture


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="flowpii",
        description="辨識 BIF/DFD 圖並產出 ISO 27701 個資盤點清冊 Excel",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_rec = sub.add_parser("recognize", help="上傳/指定圖檔並產出 Excel")
    p_rec.add_argument("input", type=Path, help="PDF / JPG / PNG")
    p_rec.add_argument("-o", "--output", type=Path, required=True, help="輸出 xlsx")
    p_rec.add_argument("--json-out", type=Path, help="同時輸出辨識 JSON")
    p_rec.add_argument("--fixture", type=Path, help="略過 AI，改用 fixture JSON")
    p_rec.add_argument("--dpi", type=int, default=200)

    p_exp = sub.add_parser("expand", help="從 graph JSON 展開並寫 Excel")
    p_exp.add_argument("graph", type=Path)
    p_exp.add_argument("-o", "--output", type=Path, required=True)

    args = parser.parse_args(argv)

    if args.cmd == "recognize":
        if args.fixture:
            # Pass input PDF when available so unlabeled「各單位」detection can run
            pdf_path = args.input if args.input.suffix.lower() == ".pdf" else None
            result = recognize_from_fixture(args.fixture, pdf_path=pdf_path)
        else:
            result = recognize_file(args.input, dpi=args.dpi)
        write_inventory_excel(
            result.rows,
            args.output,
            graph=result.graph,
        )
        if args.json_out:
            args.json_out.write_text(
                result.graph.model_dump_json(by_alias=True, indent=2),
                encoding="utf-8",
            )
        print(f"rows={len(result.rows)} -> {args.output}")
        for w in result.warnings:
            print(f"warning: {w}", file=sys.stderr)
        return 0

    if args.cmd == "expand":
        data = json.loads(args.graph.read_text(encoding="utf-8"))
        graph = BifGraph.model_validate(data)
        rows = expand_graph(graph)
        write_inventory_excel(rows, args.output, graph=graph)
        print(f"rows={len(rows)} -> {args.output}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
