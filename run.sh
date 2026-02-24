python3 scripts/eval.py \
    --hf-dataset ibragim-bad/test-21-02 \
    --hf-config default \
    --hf-split train \
    --max-workers 6 \
    --golden-eval \
    --report-json eval_report.json