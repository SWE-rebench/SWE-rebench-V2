"""Load SWE-rebench-V2.1 rows from local parquet files or from the Hugging Face Hub."""

from __future__ import annotations

from pathlib import Path


def is_harbor_row(row: dict) -> bool:
    return "workdir" in row and "test_files" in row


def load_parquet_rows(paths: list[Path]) -> list[dict]:
    import pyarrow.parquet as pq

    files: list[Path] = []
    for path in paths:
        files.extend(sorted(path.rglob("*.parquet")) if path.is_dir() else [path])
    if not files:
        raise ValueError(f"No parquet files found in {', '.join(map(str, paths))}.")
    return [row for file in files for row in pq.read_table(file).to_pylist()]


def load_hub_rows(dataset: str, revision: str | None = None) -> list[dict]:
    from huggingface_hub import snapshot_download

    local_dir = snapshot_download(
        repo_id=dataset,
        repo_type="dataset",
        revision=revision,
        allow_patterns=["data/*.parquet"],
    )
    return load_parquet_rows([Path(local_dir) / "data"])
