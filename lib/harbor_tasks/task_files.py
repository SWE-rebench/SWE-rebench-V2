"""Render a SWE-rebench-V2.1 dataset row into the files of its Harbor task.

The rendered files are byte-identical to the tasks published on the Harbor Hub,
so `scripts/eval.py` and `scripts/to_harbor.py` run exactly the published checks.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"
GRADERS = HERE / "graders"

DEFAULT_ORG = "ibragim-badertdinov"
AUTHOR_NAME = "Nebius AI R&D"
AUTHOR_EMAIL = "ibragim-bad@nebius.com"


def _quote(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def grader_source(log_parser: str) -> str:
    path = GRADERS / f"{log_parser}.py"
    if not path.is_file():
        raise ValueError(f"No grader for log parser {log_parser!r}.")
    return path.read_text(encoding="utf-8")


def test_sh(row: dict) -> str:
    template = (TEMPLATES / "test.sh").read_text(encoding="utf-8")
    return (
        template.replace("@@WORKDIR@@", row["workdir"])
        .replace("@@TEST_FILES@@", " ".join(shlex.quote(f) for f in row["test_files"]))
        .replace("@@TEST_ENV@@", "".join(f"export {e['name']}={e['value']}\n" for e in row.get("test_env") or []))
        .replace("@@TEST_CMD@@", row["test_cmd"])
    )


def solve_sh(row: dict) -> str:
    template = (TEMPLATES / "solve.sh").read_text(encoding="utf-8")
    return template.replace("@@WORKDIR@@", row["workdir"])


def config_json(row: dict) -> str:
    config = {"FAIL_TO_PASS": list(row["FAIL_TO_PASS"]), "PASS_TO_PASS": list(row["PASS_TO_PASS"])}
    return json.dumps(config, indent=1, ensure_ascii=False) + "\n"


def task_toml(row: dict, org: str = DEFAULT_ORG) -> str:
    lines = [
        "[task]",
        f"name = {_quote(org + '/' + row['instance_id'])}",
        f"authors = [{{ name = {_quote(AUTHOR_NAME)}, email = {_quote(AUTHOR_EMAIL)} }}]",
        "",
        "[metadata]",
        f"author_name = {_quote(AUTHOR_NAME)}",
        f"author_email = {_quote(AUTHOR_EMAIL)}",
        'category = "debugging"',
        f"tags = {_quote(list(row['tags']))}",
        f"repo = {_quote(row['repo'])}",
        f"language = {_quote(row['language'])}",
        f"log_parser = {_quote(row['log_parser'])}",
        f"quality_flags = {_quote(list(row['quality_flags']))}",
        "",
        "[verifier]",
        "timeout_sec = 2400",
        "",
        "[agent]",
        "timeout_sec = 3000",
        "",
        "[environment]",
        "build_timeout_sec = 1800.0",
        "cpus = 6",
        "memory_mb = 24576",
        "storage_mb = 32768",
        f"workdir = {_quote(row['workdir'])}",
        f"docker_image = {_quote(row['image_name'])}",
    ]
    env = row.get("env") or []
    if env:
        pairs = ", ".join(f"{item['name']} = {_quote(item['value'])}" for item in env)
        lines.append(f"env = {{ {pairs} }}")
    return "\n".join(lines) + "\n"


def verifier_files(row: dict) -> dict[str, str]:
    return {
        "tests/test.sh": test_sh(row),
        "tests/test.patch": row["test_patch"],
        "tests/config.json": config_json(row),
        "tests/grade.py": grader_source(row["log_parser"]),
    }


def solution_files(row: dict, patch: str | None = None) -> dict[str, str]:
    return {
        "solution/solve.sh": solve_sh(row),
        "solution/gold.patch": row["patch"] if patch is None else patch,
    }


def task_files(row: dict, org: str = DEFAULT_ORG) -> dict[str, str]:
    return {
        "task.toml": task_toml(row, org),
        "instruction.md": row["problem_statement"],
        **solution_files(row),
        **verifier_files(row),
    }


def write_task(row: dict, out_dir: Path, org: str = DEFAULT_ORG) -> Path:
    task_dir = out_dir / row["instance_id"]
    for rel, content in task_files(row, org).items():
        path = task_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")
        if path.suffix == ".sh":
            path.chmod(0o755)
    return task_dir
