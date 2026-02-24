#!/usr/bin/env python3
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from jinja2 import Environment, StrictUndefined, meta
from openai import OpenAI


DEFAULT_PROMPT_TEMPLATE = Path("prompts/annotations/pr_description.j2")
DEFAULT_META_TEMPLATE = Path("prompts/annotations/meta_info.j2")
DEFAULT_INPUT = Path("sample.json")
DEFAULT_OUTPUT = Path("rendered_prompts.json")


def load_records(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    raise ValueError(f"Expected list or object in JSON: {path}")


def get_template_vars(template_text: str) -> set[str]:
    env = Environment()
    parsed = env.parse(template_text)
    return set(meta.find_undeclared_variables(parsed))


def render_template(records: list[dict], template_text: str, template_vars: set[str]) -> list[str]:
    env = Environment(undefined=StrictUndefined)
    template = env.from_string(template_text)

    rendered = []
    for idx, record in enumerate(records):
        missing = sorted(var for var in template_vars if var not in record)
        if missing:
            missing_str = ", ".join(missing)
            raise KeyError(
                f"Record {idx} is missing template fields: {missing_str}. "
                "Update the Jinja template or add fields to the input JSON."
            )
        rendered.append(template.render(**record))
    return rendered


def request_one(client: OpenAI, model: str, prompt: str) -> dict:
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": ""},
                {"role": "user", "content": prompt},
            ],
        )
        content = ""
        if completion.choices and completion.choices[0].message:
            content = completion.choices[0].message.content or ""
        return {"response": completion.to_dict(), "raw": content, "error": None}
    except Exception as exc:
        return {"response": None, "raw": "", "error": str(exc)}

def render_progress(done: int, total: int, bar_width: int = 30) -> str:
    if total <= 0:
        return "Progress: 0/0"
    filled = int(bar_width * done / total)
    bar = "#" * filled + "-" * (bar_width - filled)
    return f"\rProgress: {done}/{total} [{bar}]"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render a Jinja prompt using JSON records and save to a new JSON file."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help="Input JSON file (default: sample.json).",
    )
    parser.add_argument(
        "--prompt-template",
        default=str(DEFAULT_PROMPT_TEMPLATE),
        help="Prompt template path (default: prompts/annotations/pr_description.j2).",
    )
    parser.add_argument(
        "--meta-template",
        default=str(DEFAULT_META_TEMPLATE),
        help="Meta template path (default: prompts/annotations/meta_info.j2).",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output JSON file (default: rendered_prompts.json).",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Send rendered prompts to OpenAI API.",
    )
    parser.add_argument(
        "--field",
        choices=["prompt", "meta_prompt", "both"],
        default="prompt",
        help="Which field to send when --send is set (default: prompt).",
    )
    parser.add_argument("--model", help="OpenAI model name when --send is set.")
    parser.add_argument("--api-base", help="OpenAI API base URL.")
    parser.add_argument("--api-key", help="OpenAI API key.")
    parser.add_argument(
        "--max-workers",
        type=int,
        default=50,
        help="Max workers for API requests (default: 50).",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    prompt_template_path = Path(args.prompt_template)
    meta_template_path = Path(args.meta_template)
    output_path = Path(args.output)

    if not input_path.exists():
        raise FileNotFoundError(f"Input JSON not found: {input_path}")
    if not prompt_template_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {prompt_template_path}")
    if not meta_template_path.exists():
        raise FileNotFoundError(f"Meta template not found: {meta_template_path}")

    records = load_records(input_path)
    prompt_template_text = prompt_template_path.read_text()
    prompt_template_vars = get_template_vars(prompt_template_text)
    meta_template_text = meta_template_path.read_text()
    meta_template_vars = get_template_vars(meta_template_text)

    prompt_rendered = render_template(records, prompt_template_text, prompt_template_vars)
    meta_rendered = render_template(records, meta_template_text, meta_template_vars)

    rendered = []
    for record, prompt, meta_prompt in zip(records, prompt_rendered, meta_rendered):
        enriched = dict(record)
        enriched["prompt"] = prompt
        enriched["meta_prompt"] = meta_prompt
        rendered.append(enriched)

    if args.send:
        if not args.model:
            raise ValueError("--model is required when --send is set.")
        if not args.api_base:
            raise ValueError("--api-base is required when --send is set.")
        if not args.api_key:
            raise ValueError("--api-key is required when --send is set.")

        client = OpenAI(api_key=args.api_key, base_url=args.api_base)

        field_map = {
            "prompt": lambda item: [("prompt", item["prompt"])],
            "meta_prompt": lambda item: [("meta_prompt", item["meta_prompt"])],
            "both": lambda item: [("prompt", item["prompt"]), ("meta_prompt", item["meta_prompt"])],
        }

        requests = []
        for idx, item in enumerate(rendered):
            for field_name, prompt in field_map[args.field](item):
                requests.append((idx, field_name, prompt))

        results: dict[tuple[int, str], dict] = {}
        completed = 0
        total = len(requests)
        with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            futures = {
                executor.submit(request_one, client, args.model, prompt): (idx, field_name)
                for idx, field_name, prompt in requests
            }
            for future in as_completed(futures):
                key = futures[future]
                results[key] = future.result()
                completed += 1
                sys.stderr.write(render_progress(completed, total))
                sys.stderr.flush()
        if total:
            sys.stderr.write("\n")

        for idx, item in enumerate(rendered):
            if args.field in ("prompt", "both"):
                item["prompt_response"] = results[(idx, "prompt")]
            if args.field in ("meta_prompt", "both"):
                item["meta_prompt_response"] = results[(idx, "meta_prompt")]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(rendered, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
