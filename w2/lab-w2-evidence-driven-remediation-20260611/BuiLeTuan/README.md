# Evidence-Driven Remediation Engine

This submission implements a three-stage remediation pipeline:

1. Extract comparable log, trace, metric, and service-topology features.
2. Retrieve the three closest historical incidents and perform
   outcome-weighted action voting.
3. Select an action with confidence, utility, OOD, trace-consistency, and
   blast-radius safety gates.

## Requirements

- Windows PowerShell
- Python 3.12
- `uv`

## Setup

From the `submission` directory:

```powershell
$env:UV_CACHE_DIR="$PWD\.uv-cache"
uv venv --python 3.12
.\.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt
```

## Run One Incident

```powershell
python engine.py decide `
  --incident ../data-pack/eval/E01.json `
  --history ../data-pack/incidents_history.json `
  --actions ../data-pack/actions.yaml
```

The command prints the decision as JSON and appends one JSON object to
`audit.jsonl`.

## Run All Evaluation Incidents

Clear the previous audit before a reproducible full run:

```powershell
Clear-Content audit.jsonl

1..8 | ForEach-Object {
    $id = "E{0:D2}" -f $_
    python engine.py decide `
      --incident "../data-pack/eval/$id.json" `
      --history ../data-pack/incidents_history.json `
      --actions ../data-pack/actions.yaml
}
```

## Grade

```powershell
python ../data-pack/grade.py `
  --audit audit.jsonl `
  --expected ../data-pack/eval/expected.json
```

The included `audit.jsonl` contains one result for each incident E01-E08.
