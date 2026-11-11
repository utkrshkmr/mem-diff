# mem-diff

```
git clone https://github.com/utkrshkmr/mem-diff.git && cd mem-diff
bash scripts/launch.sh configs/smoke.yaml
bash scripts/status.sh
bash scripts/launch.sh configs/pilot.yaml
bash scripts/launch.sh configs/full.yaml
bash scripts/stop.sh all
```

| var | default |
|---|---|
| MD_SERVE | docker (or native) |
| MD_VLLM_IMAGE | vllm/vllm-openai:latest |
| MD_VLLM_BIN | vllm |
| MD_VLLM_EXTRA | |
| MD_PUSH_EVERY | 1800 |
| MD_KEEP_SERVERS | 0 |
| HF_TOKEN, HF_HOME | |

Local check: `pip install -r requirements.txt && pytest -q && python -m memdiff.cli run configs/mock.yaml`
