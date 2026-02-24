# SWE-rebench-v2 builder

Tools and prompt templates used to build and evaluate SWE-rebench-v2 tasks for the paper. This repo covers:

- **Labeling prompts** used for annotations (PR description, meta info, interfaces, log parser tasks)
- **Dockerfile generation** (base images + per-task instance images)
- **Task evaluation** using generated instance images and log parsers

## Repository layout

- `prompts/annotations/` — main labeling prompts (Jinja templates)
  - `pr_description.j2` — PR description generation prompt
  - `meta_info.j2` — meta info classification prompt
  - `interfaces.j2` — interface extraction prompt
- `prompts/installer/` — builder/installer prompts
  - `log_parser.j2` — log parser synthesis prompt
  - `base_dockerfile.j2` — base image generation prompt
  - `agent.j2` — installer agent prompt
- `prompts/issue_clarity_ablation/` — issue clarity ablation variants
- `combine.Dockerfile.j2` — Jinja template for per-task instance Dockerfiles
- `scripts/annotation_script.py` — render prompts from JSON to `prompt` / `meta_prompt`
- `scripts/build_base_images.py` — build base Docker images from `base_dockerfiles/`
- `scripts/build_instance_images.py` — render + build per-task images
- `scripts/eval.py` — run tasks in Docker and parse test logs
- `lib/agent/log_parsers.py` — parsers referenced by tasks
- `sample.json` — example task list
- `issue_based_tasks_sample.json` — issue-based task samples
- `pr_based_tasks_sample.json` — PR-based task samples

## Requirements

- Python 3.10+ recommended
- Docker
- Python deps:

```bash
pip install -r requirements.txt
```

Note: `openai` is required for the `--send` mode in `scripts/annotation_script.py`.

## Labeling prompt generation

Render both PR description prompt and meta prompt into a single JSON:

```bash
python3 scripts/annotation_script.py \
  --input sample.json \
  --prompt-template prompts/annotations/pr_description.j2 \
  --meta-template prompts/annotations/meta_info.j2 \
  --output rendered_prompts.json
```

The output keeps original fields and adds:
- `prompt` — rendered PR description prompt
- `meta_prompt` — rendered meta info prompt

Optional: send rendered prompts to OpenAI API and store responses:

```bash
python3 scripts/annotation_script.py \
  --input sample.json \
  --prompt-template prompts/annotations/pr_description.j2 \
  --meta-template prompts/annotations/meta_info.j2 \
  --output rendered_prompts.json \
  --send \
  --field both \
  --model gpt-4.1-mini \
  --api-base https://api.openai.com/v1 \
  --api-key $OPENAI_API_KEY
```

Additional output fields when `--send` is used:
- `prompt_response` — OpenAI response (includes `raw`, `error`, and full response payload)
- `meta_prompt_response` — same for `meta_prompt` when `--field meta_prompt` or `--field both`

## Build base Docker images

Use the prebuilt Dockerfiles under `base_dockerfiles/`:

```bash
python3 scripts/build_base_images.py --dockerfiles-dir base_dockerfiles
```

## Build per-task Docker images

Render and build instance images for all tasks in `sample.json`:

```bash
python3 scripts/build_instance_images.py \
  --json sample.json \
  --template combine.Dockerfile.j2 \
  --output-dir dockerfiles
```

Optional flags:
- `--base-image-registry` for base image registry prefix
- `--image-registry` and `--tag-prefix` for instance image tags

## Run evaluation on all tasks (sample.json)

This runs each task with some predicted patches in its instance Docker image, applies patches, runs tests, and parses logs:

```bash
python3 scripts/eval.py --json sample.json --patches patches.json
```

Notes:
- Instance image tag is derived from each `instance_id`.
- Ensure you built the instance images first (see above).
- Logs are saved as `<instance_id>_log.txt` in the repo root.

## Prompts used for labeling

Primary prompts live in `prompts/annotations/`:
- `pr_description.j2` — PR description generation
- `meta_info.j2` — meta classification (A/B codes + difficulty)
- `interfaces.j2` — interface extraction tied to tests

Installer/build prompts live in `prompts/installer/`:
- `log_parser.j2` — log parser synthesis
- `base_dockerfile.j2` — base image generation
- `agent.j2` — installer agent prompt

Issue clarity ablations are under `prompts/issue_clarity_ablation/`:
- `verified.j2`, `verified_plus.j2`, `verified_extra.j2`, `spice.j2`, `rebench_v1.j2`
