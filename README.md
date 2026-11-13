# SWE-rebench-v2 builder

Tools and prompt templates used to build and evaluate SWE-rebench-v2 tasks for the paper. This repo covers:

- **Labeling prompts** used for annotations (PR description, meta info, interfaces, log parser tasks)
- **Dockerfile generation** (base images + per-task instance images)
- **Task evaluation** using generated instance images and log parsers

## How to Run Eval with Existing Docker Images

1. Using a local sample JSON for debugging:

```bash
python3 scripts/eval.py \
  --json sample.json \
  --max-workers 1 \
  --golden-eval \
  --report-json eval_report.json
```

2. Using the 20-task sample from Hugging Face:

```bash
python3 scripts/eval.py \
  --hf-dataset ibragim-bad/SWE-rebench-V2-sample \
  --hf-config default \
  --hf-split train \
  --max-workers 8 \
  --golden-eval \
  --report-json eval_report.json
```

## SWE-rebench V2.1

[SWE-rebench V2.1](https://huggingface.co/datasets/ibragim-bad/SWE-rebench-V2.1) contains 21,272 re-verified
tasks: SWE-rebench V2 tasks with repaired statements, tests, images and log parsers, plus 7,935 Python tasks
from the first SWE-rebench. Each row is the content of one Harbor task, so evaluation runs the Harbor
verifier: `solution/solve.sh` applies the patch, then `tests/test.sh` restores `test_files`, applies
`test_patch`, runs `test_cmd` and grades the log with the standalone grader for `log_parser`. Reward is 1 when
every `FAIL_TO_PASS` and `PASS_TO_PASS` test passes; tests outside these lists are ignored.

```bash
python3 scripts/eval.py \
  --hf-dataset ibragim-bad/SWE-rebench-V2.1 \
  --instance-ids aio-libs__aiohttp-10300,0b01001001__spectree-64 \
  --max-workers 2 \
  --golden-eval \
  --report-json eval_report.json
```

`--patches` works as for V2 rows. `--parquet <files or dirs>` reads a local copy instead of the Hub. When the
dataset viewer is unavailable (for example, for a private dataset), `eval.py` downloads the parquet files with
`huggingface_hub`; set `HF_TOKEN` or run `hf auth login` for private datasets.

Convert rows to Harbor task directories, byte-identical to the tasks published on the Harbor Hub:

```bash
python3 scripts/to_harbor.py --hf-dataset ibragim-bad/SWE-rebench-V2.1 --out tasks
harbor run -p tasks/aio-libs__aiohttp-10300 -a oracle
```

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
- `scripts/to_harbor.py` — write SWE-rebench V2.1 rows as Harbor task directories
- `lib/harbor_tasks/` — Harbor task templates, standalone graders and row loaders for SWE-rebench V2.1
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

# Citation

```bibtex
@misc{badertdinov2026swerebenchv2languageagnosticswe,
      title={SWE-rebench V2: Language-Agnostic SWE Task Collection at Scale}, 
      author={Ibragim Badertdinov and Maksim Nekrashevich and Anton Shevtsov and Alexander Golubev},
      year={2026},
      eprint={2602.23866},
      archivePrefix={arXiv},
      primaryClass={cs.SE},
      url={https://arxiv.org/abs/2602.23866}, 
}
