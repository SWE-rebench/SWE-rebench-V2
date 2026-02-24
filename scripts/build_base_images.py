#!/usr/bin/env python3
import argparse
import re
import subprocess
import sys
from pathlib import Path


def derive_image_name(filename: str, tag_suffix: str) -> str:
    name = re.sub(r"dockerfile", "", filename, flags=re.IGNORECASE)
    name = name.strip("._-")
    if not name:
        name = filename
    if tag_suffix and not name.endswith(tag_suffix):
        name = f"{name}{tag_suffix}"
    return name


def build_image(dockerfile: Path, context_dir: Path, tag: str, platform: str | None) -> None:
    cmd = ["docker", "build", "-f", str(dockerfile), "-t", tag]
    if platform:
        cmd.extend(["--platform", platform])
    cmd.append(str(context_dir))
    subprocess.run(cmd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build base docker images.")
    parser.add_argument(
        "--dockerfiles-dir",
        default="base_dockerfiles",
        help="Directory with base Dockerfiles.",
    )
    parser.add_argument(
        "--context-dir",
        default=None,
        help="Build context directory (defaults to --dockerfiles-dir).",
    )
    parser.add_argument(
        "--registry",
        default="",
        help="Optional image registry/repo prefix (e.g. myrepo).",
    )
    parser.add_argument(
        "--tag-suffix",
        default="_base",
        help="Suffix appended to image names derived from filenames.",
    )
    parser.add_argument(
        "--platform",
        default="linux/amd64",
        help="Target platform for docker build.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print docker commands without running them.",
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Continue building other images on error.",
    )
    args = parser.parse_args()

    dockerfiles_dir = Path(args.dockerfiles_dir)
    if not dockerfiles_dir.is_dir():
        print(f"Directory not found: {dockerfiles_dir}", file=sys.stderr)
        return 1

    context_dir = Path(args.context_dir) if args.context_dir else dockerfiles_dir
    if not context_dir.is_dir():
        print(f"Context directory not found: {context_dir}", file=sys.stderr)
        return 1

    dockerfiles = sorted(
        p
        for p in dockerfiles_dir.iterdir()
        if p.is_file() and "dockerfile" in p.name.lower()
    )
    if not dockerfiles:
        print(f"No Dockerfiles found in {dockerfiles_dir}", file=sys.stderr)
        return 1

    failures = 0
    for dockerfile in dockerfiles:
        image_name = derive_image_name(dockerfile.name, args.tag_suffix)
        tag = f"{args.registry}/{image_name}" if args.registry else image_name

        if args.dry_run:
            cmd = ["docker", "build", "-f", str(dockerfile), "-t", tag]
            if args.platform:
                cmd.extend(["--platform", args.platform])
            cmd.append(str(context_dir))
            print(" ".join(cmd))
            continue

        try:
            build_image(dockerfile, context_dir, tag, args.platform)
        except subprocess.CalledProcessError:
            failures += 1
            print(f"Failed to build {dockerfile.name}", file=sys.stderr)
            if not args.keep_going:
                return 1

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
