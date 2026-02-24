#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:  # pragma: no cover
    print("Missing dependency: jinja2. Install with `pip install jinja2`.", file=sys.stderr)
    raise


def render_dockerfile(template_path: Path, output_path: Path, spec: dict, registry: str) -> None:
    env = Environment(loader=FileSystemLoader(str(template_path.parent)), autoescape=False)
    template = env.get_template(template_path.name)
    content = template.render(spec=spec, base_image_registry=registry, platform="linux/amd64")
    output_path.write_text(content, encoding="utf-8")


def build_image(dockerfile: Path, context_dir: Path, tag: str) -> None:
    cmd = ["docker", "build", "-f", str(dockerfile), "-t", tag, str(context_dir)]
    subprocess.run(cmd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render and build instance images from JSON.")
    parser.add_argument("--json", required=True, help="Path to JSON file with tasks.")
    parser.add_argument(
        "--template",
        default="combine.Dockerfile.j2",
        help="Path to Jinja2 Dockerfile template.",
    )
    parser.add_argument(
        "--output-dir",
        default="dockerfiles",
        help="Directory to write rendered Dockerfiles.",
    )
    parser.add_argument(
        "--base-image-registry",
        default="",
        help="Registry/repo prefix for base images used in template.",
    )
    parser.add_argument(
        "--image-registry",
        default="",
        help="Optional registry/repo prefix for built instance images.",
    )
    parser.add_argument(
        "--tag-prefix",
        default="",
        help="Optional prefix for instance image tags.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render Dockerfiles but skip docker build.",
    )
    args = parser.parse_args()

    json_path = Path(args.json)
    if not json_path.is_file():
        print(f"JSON file not found: {json_path}", file=sys.stderr)
        return 1

    template_path = Path(args.template)
    if not template_path.is_file():
        print(f"Template not found: {template_path}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    data = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        print("JSON root must be a list of tasks.", file=sys.stderr)
        return 1

    for spec in data:
        if not isinstance(spec, dict):
            print("Each task must be an object.", file=sys.stderr)
            return 1
        instance_id = spec.get("instance_id")
        if not instance_id:
            print("Task missing instance_id.", file=sys.stderr)
            return 1

        dockerfile_path = output_dir / f"{instance_id}.Dockerfile"
        render_dockerfile(template_path, dockerfile_path, spec, args.base_image_registry)

        if args.dry_run:
            print(f"Rendered {dockerfile_path}")
            continue

        tag = f"{args.tag_prefix}{instance_id}"
        if args.image_registry:
            tag = f"{args.image_registry}/{tag}"
        build_image(dockerfile_path, output_dir, tag)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
