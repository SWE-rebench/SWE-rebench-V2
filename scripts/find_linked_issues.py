#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

API_ROOT = "https://api.github.com"
ISSUE_URL_RE = re.compile(r"https?://github\.com/([^/]+)/([^/]+)/issues/(\d+)")
API_ISSUE_URL_RE = re.compile(r"https?://api\.github\.com/repos/([^/]+)/([^/]+)/issues/(\d+)")
CLOSES_RE = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?[\s]+"
    r"(?:(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#(?P<num>\d+)|"
    r"https?://github\.com/(?P<url_repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/(?P<url_num>\d+)|"
    r"#(?P<local_num>\d+))"
)


@dataclass(frozen=True)
class PullRef:
    owner: str
    repo: str
    number: int

    @property
    def full_repo(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def key(self) -> str:
        return f"{self.full_repo}#{self.number}"


def parse_repo(repo_raw: str) -> Tuple[str, str]:
    if not repo_raw or "/" not in repo_raw:
        raise ValueError(f"invalid repo value: {repo_raw!r}")
    owner, name = repo_raw.split("/", 1)
    return owner.strip(), name.strip()


def find_first(record: dict, keys: Iterable[str]):
    for key in keys:
        if key in record and record[key] is not None:
            return record[key]
    return None


def load_pull_refs(input_path: str, limit: Optional[int]) -> List[PullRef]:
    with open(input_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, list):
        raise ValueError("Expected top-level JSON array")

    refs: List[PullRef] = []
    for idx, rec in enumerate(raw):
        if not isinstance(rec, dict):
            continue
        repo_raw = find_first(rec, ["repo", "repository", "repo_name"])
        pr_raw = find_first(rec, ["pull_number", "pr", "pr_number", "pull_request_number"])
        if repo_raw is None or pr_raw is None:
            continue
        try:
            owner, repo = parse_repo(str(repo_raw))
            number = int(pr_raw)
        except Exception as exc:
            print(f"skip record #{idx}: bad repo/pull fields ({exc})", file=sys.stderr)
            continue
        refs.append(PullRef(owner=owner, repo=repo, number=number))

    uniq: Dict[str, PullRef] = {}
    for ref in refs:
        uniq[ref.key] = ref

    deduped = list(uniq.values())
    deduped.sort(key=lambda r: (r.full_repo.lower(), r.number))

    if limit is not None:
        deduped = deduped[:limit]
    return deduped


def github_request(path: str, token: Optional[str]) -> Tuple[dict, Dict[str, str]]:
    url = f"{API_ROOT}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "pr-issue-linker-script",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = Request(url, headers=headers)

    while True:
        try:
            with urlopen(req) as resp:
                body = resp.read().decode("utf-8")
                data = json.loads(body)
                return data, {k.lower(): v for k, v in resp.headers.items()}
        except HTTPError as e:
            hdrs = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
            remaining = hdrs.get("x-ratelimit-remaining")
            reset = hdrs.get("x-ratelimit-reset")
            if e.code in (403, 429) and remaining == "0" and reset:
                try:
                    wait = max(1, int(reset) - int(time.time()) + 1)
                except ValueError:
                    wait = 60
                print(f"rate limit hit, sleeping {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            text = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"GitHub API error {e.code} for {path}: {text[:500]}") from e
        except URLError as e:
            raise RuntimeError(f"Network error for {path}: {e}") from e


def parse_next_link(link_header: Optional[str]) -> Optional[str]:
    if not link_header:
        return None
    for part in link_header.split(","):
        part = part.strip()
        if 'rel="next"' in part:
            m = re.match(r"<([^>]+)>", part)
            if m:
                url = m.group(1)
                if url.startswith(API_ROOT):
                    return url[len(API_ROOT):]
                return url
    return None


def fetch_timeline(ref: PullRef, token: Optional[str]) -> List[dict]:
    path = f"/repos/{quote(ref.owner)}/{quote(ref.repo)}/issues/{ref.number}/timeline?per_page=100"
    events: List[dict] = []
    while path:
        data, headers = github_request(path, token)
        if isinstance(data, list):
            events.extend(x for x in data if isinstance(x, dict))
        else:
            break
        path = parse_next_link(headers.get("link"))
    return events


def fetch_pull_body(ref: PullRef, token: Optional[str]) -> str:
    path = f"/repos/{quote(ref.owner)}/{quote(ref.repo)}/pulls/{ref.number}"
    data, _ = github_request(path, token)
    if not isinstance(data, dict):
        return ""
    body = data.get("body")
    return body if isinstance(body, str) else ""


def walk_strings(obj) -> Iterable[str]:
    if isinstance(obj, dict):
        for v in obj.values():
            yield from walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_strings(v)
    elif isinstance(obj, str):
        yield obj


def extract_issues_from_timeline(events: List[dict], default_repo: str) -> Set[str]:
    issues: Set[str] = set()
    for ev in events:
        for s in walk_strings(ev):
            for m in ISSUE_URL_RE.finditer(s):
                issues.add(f"{m.group(1)}/{m.group(2)}#{m.group(3)}")
            for m in API_ISSUE_URL_RE.finditer(s):
                issues.add(f"{m.group(1)}/{m.group(2)}#{m.group(3)}")
    return issues


def extract_issues_from_body(body: str, default_repo: str) -> Set[str]:
    issues: Set[str] = set()
    if not body:
        return issues

    for m in CLOSES_RE.finditer(body):
        if m.group("repo") and m.group("num"):
            issues.add(f"{m.group('repo')}#{m.group('num')}")
            continue
        if m.group("url_repo") and m.group("url_num"):
            issues.add(f"{m.group('url_repo')}#{m.group('url_num')}")
            continue
        if m.group("local_num"):
            issues.add(f"{default_repo}#{m.group('local_num')}")

    return issues


def normalize_issue_ids(raw_ids: Iterable[str]) -> List[str]:
    normalized = set()
    for iid in raw_ids:
        if "#" not in iid or "/" not in iid:
            continue
        repo, num = iid.rsplit("#", 1)
        repo = repo.strip().lower()
        try:
            num_i = int(num)
        except ValueError:
            continue
        if num_i <= 0:
            continue
        normalized.add(f"{repo}#{num_i}")
    return sorted(normalized)


def process_ref(ref: PullRef, token: Optional[str]) -> dict:
    issues = set()
    timeline = fetch_timeline(ref, token)
    issues.update(extract_issues_from_timeline(timeline, ref.full_repo))

    body = fetch_pull_body(ref, token)
    issues.update(extract_issues_from_body(body, ref.full_repo))

    linked = normalize_issue_ids(issues)
    return {
        "repo": ref.full_repo,
        "pull_number": ref.number,
        "pull_key": ref.key,
        "pull_url": f"https://github.com/{ref.full_repo}/pull/{ref.number}",
        "linked_issues": linked,
        "linked_issues_count": len(linked),
    }


def print_multi_issue_prs(rows: List[dict]) -> None:
    if not rows:
        print("No pull requests linked to more than one issue.")
        return
    print("Pull requests linked to >1 issue:")
    for row in rows:
        print(f"- {row['pull_key']} ({row['linked_issues_count']} issues)")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect GitHub issues linked to pull requests from a JSON dataset."
    )
    parser.add_argument(
        "--input",
        default="/Users/ibragim-bad/Downloads/midrange_400.json",
        help="Path to input JSON list",
    )
    parser.add_argument(
        "--output-all",
        default="pr_issue_links.json",
        help="Path to write all PR->issues results",
    )
    parser.add_argument(
        "--output-multi",
        default="prs_with_multiple_issues.json",
        help="Path to write PRs linked with more than one issue",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit of unique PRs to process",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="Sleep in seconds between PR API fetches",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write files; print summary only",
    )
    parser.add_argument(
        "--allow-unauthenticated",
        action="store_true",
        help="Allow running without GITHUB_TOKEN (may hit strict rate limits)",
    )

    args = parser.parse_args()
    token = os.getenv("GITHUB_TOKEN")
    if not token and not args.allow_unauthenticated:
        print(
            "GITHUB_TOKEN is required. Export it or pass --allow-unauthenticated "
            "to run with low API limits.",
            file=sys.stderr,
        )
        return 2

    refs = load_pull_refs(args.input, args.limit)
    if not refs:
        print("No pull requests found in input.", file=sys.stderr)
        return 1

    print(f"Processing {len(refs)} pull requests...")
    results: List[dict] = []
    failures = 0

    for idx, ref in enumerate(refs, start=1):
        try:
            row = process_ref(ref, token)
            results.append(row)
            print(f"[{idx}/{len(refs)}] {ref.key}: {row['linked_issues_count']} linked issues")
        except Exception as exc:
            failures += 1
            print(f"[{idx}/{len(refs)}] {ref.key}: ERROR: {exc}", file=sys.stderr)
        if args.sleep > 0:
            time.sleep(args.sleep)

    multi = [r for r in results if r.get("linked_issues_count", 0) > 1]
    multi.sort(key=lambda r: (-r["linked_issues_count"], r["pull_key"]))

    print_multi_issue_prs(multi)

    if not args.dry_run:
        with open(args.output_all, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        with open(args.output_multi, "w", encoding="utf-8") as f:
            json.dump(multi, f, ensure_ascii=False, indent=2)
        print(f"Wrote: {args.output_all}")
        print(f"Wrote: {args.output_multi}")

    print(f"Done. Success: {len(results)}, failures: {failures}")
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
