#!/usr/bin/env python3
"""Write SWE-rebench-V2.1 rows as Harbor task directories.

The generated tasks are byte-identical to the tasks published on the Harbor Hub and can be run
locally with `harbor run -p <out>/<instance_id>` or `harbor run -p <out>`.
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "lib"))

from harbor_tasks.rows import load_hub_rows, load_parquet_rows  # noqa: E402
from harbor_tasks.task_files import DEFAULT_ORG, write_task  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--hf-dataset", help="Hub dataset id, e.g. ibragim-bad/SWE-rebench-V2.1.")
    source.add_argument("--parquet", type=Path, nargs="+", help="Local parquet files or directories.")
    parser.add_argument("--hf-revision", default=None, help="Hub dataset revision (branch, tag or commit).")
    parser.add_argument("--out", type=Path, required=True, help="Output directory for task directories.")
    parser.add_argument("--instance-ids", default="", help="Comma-separated subset of instance ids.")
    parser.add_argument("--org", default=DEFAULT_ORG, help="Harbor organization used in task names.")
    args = parser.parse_args()

    rows = load_hub_rows(args.hf_dataset, args.hf_revision) if args.hf_dataset else load_parquet_rows(args.parquet)
    wanted = {x.strip() for x in args.instance_ids.split(",") if x.strip()}
    if wanted:
        rows = [row for row in rows if row["instance_id"] in wanted]
        missing = wanted - {row["instance_id"] for row in rows}
        if missing:
            print(f"Unknown instance ids: {', '.join(sorted(missing))}", file=sys.stderr)
            return 1

    for row in rows:
        write_task(row, args.out, args.org)
    print(f"Wrote {len(rows)} Harbor tasks to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
