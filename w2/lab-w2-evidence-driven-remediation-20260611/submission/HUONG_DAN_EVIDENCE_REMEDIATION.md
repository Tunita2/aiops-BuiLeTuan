# Huong dan setup Evidence-Driven Remediation Engine

Tai lieu nay huong dan setup project tren Windows PowerShell, tao code khung,
chay thu CLI va chuan bi cho phan cai dat thuat toan.

## 1. Muc tieu sau khi setup

Sau khi hoan thanh, project co cau truc:

```text
lab-w2-evidence-driven-remediation-20260611/
|-- data-pack/                 # Du lieu de bai, khong sua
`-- submission/                # Bai lam cua ban
    |-- .venv/
    |-- engine.py
    |-- features.py
    |-- retrieval.py
    |-- decision.py
    |-- utils.py
    |-- requirements.txt
    |-- audit.jsonl
    |-- FINDINGS.md
    `-- README.md
```

Chuong trinh se chay bang lenh:

```powershell
python engine.py decide `
  --incident ../data-pack/eval/E01.json `
  --history ../data-pack/incidents_history.json `
  --actions ../data-pack/actions.yaml
```

## 2. Mo PowerShell tai project

Mo PowerShell va chuyen den thu muc de bai:

```powershell
cd D:\Cloude-DevOps\Phase-2\aiops-project\w2\lab-w2-evidence-driven-remediation-20260611
```

Kiem tra noi dung:

```powershell
Get-ChildItem
```

Ban phai thay thu muc `data-pack`.

Khong sua truc tiep file trong `data-pack`. Day la du lieu dau vao va grader cua
de bai.

## 3. Tao thu muc bai lam

```powershell
New-Item -ItemType Directory -Force submission
cd submission
```

Kiem tra vi tri hien tai:

```powershell
Get-Location
```

Ket qua phai ket thuc bang:

```text
\lab-w2-evidence-driven-remediation-20260611\submission
```

## 4. Tao Python virtual environment

May da co cong cu `uv`. Dat cache trong project de tranh loi quyen truy cap:

```powershell
$env:UV_CACHE_DIR="$PWD\.uv-cache"
uv venv --python 3.12
```

Lenh tren se:

1. Tai Python 3.12 neu may chua co.
2. Tao virtual environment tai `.venv`.
3. Tach dependency cua bai lam khoi Python he thong.

Kich hoat moi truong:

```powershell
.\.venv\Scripts\Activate.ps1
```

Neu PowerShell bao script execution bi chan:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
```

`-Scope Process` chi thay doi trong cua so PowerShell hien tai.

Kiem tra:

```powershell
python --version
```

Ket qua mong doi:

```text
Python 3.12.x
```

Moi lan mo PowerShell moi, can quay lai `submission` va kich hoat `.venv`.

## 5. Cai dependency

```powershell
uv pip install numpy scikit-learn pyyaml
```

Vai tro cua tung thu vien:

| Thu vien | Muc dich |
|---|---|
| `numpy` | Tinh baseline, trung binh va muc thay doi metric |
| `scikit-learn` | TF-IDF va cosine similarity cho log |
| `pyyaml` | Doc action catalog trong `actions.yaml` |

Luu danh sach dependency:

```powershell
uv pip freeze > requirements.txt
```

Kiem tra:

```powershell
Get-Content requirements.txt
```

## 6. Tao cac file code

```powershell
New-Item engine.py -Force
New-Item features.py -Force
New-Item retrieval.py -Force
New-Item decision.py -Force
New-Item utils.py -Force
New-Item audit.jsonl -Force
New-Item FINDINGS.md -Force
New-Item README.md -Force
```

Trach nhiem cua cac file:

| File | Trach nhiem |
|---|---|
| `engine.py` | CLI, doc input, goi ba layer va ghi audit |
| `features.py` | Chuyen log, trace, metric thanh feature |
| `retrieval.py` | Tim incident lich su tuong tu va vote action |
| `decision.py` | Confidence, utility, OOD va blast-radius gate |
| `utils.py` | Ham parse action, normalize text va tien ich chung |
| `audit.jsonl` | Mot JSON object tren moi dong cho moi incident |
| `FINDINGS.md` | Tra loi nam cau hoi reflection |
| `README.md` | Cach cai va chay bai lam |

## 7. Tao `engine.py`

Them noi dung sau vao `engine.py`:

```python
import argparse
import json
from pathlib import Path

import yaml

from decision import select_action
from features import extract_features
from retrieval import retrieve_and_vote


def decide(
    incident_path: Path,
    history_path: Path,
    actions_path: Path,
) -> dict:
    incident = json.loads(incident_path.read_text(encoding="utf-8"))
    history = json.loads(history_path.read_text(encoding="utf-8"))
    actions = yaml.safe_load(actions_path.read_text(encoding="utf-8"))

    query = extract_features(incident)
    candidates = retrieve_and_vote(query, history)
    result = select_action(query, candidates, actions)

    # Grader yeu cau E01, E02..., khong dung full incident_id trong JSON.
    result["incident_id"] = incident_path.stem
    return result


def append_audit(result: dict, audit_path: Path) -> None:
    with audit_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(result, ensure_ascii=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    decide_parser = subparsers.add_parser("decide")
    decide_parser.add_argument("--incident", required=True)
    decide_parser.add_argument(
        "--history",
        default="../data-pack/incidents_history.json",
    )
    decide_parser.add_argument(
        "--actions",
        default="../data-pack/actions.yaml",
    )
    decide_parser.add_argument("--audit", default="audit.jsonl")

    args = parser.parse_args()

    result = decide(
        Path(args.incident),
        Path(args.history),
        Path(args.actions),
    )
    print(json.dumps(result, indent=2, ensure_ascii=True))
    append_audit(result, Path(args.audit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

File nay chua chua thuat toan. No chi noi cac layer thanh mot pipeline.

## 8. Tao implementation gia de test pipeline

Muc tieu luc nay la xac nhan CLI, path, JSON, YAML va audit hoat dong. Chua can
dua ra action dung.

### `features.py`

```python
def extract_features(incident: dict) -> dict:
    return {
        "trigger_service": incident["trigger_alert"]["service"],
        "incident": incident,
    }
```

### `retrieval.py`

```python
def retrieve_and_vote(
    query: dict,
    history: list[dict],
    top_k: int = 3,
) -> dict:
    return {
        "top_3_neighbors": [],
        "candidate_votes": {},
        "best_similarity": 0.0,
    }
```

### `decision.py`

```python
def select_action(
    query: dict,
    candidates: dict,
    actions: list[dict],
) -> dict:
    return {
        "selected_action": "page_oncall",
        "params": {"team": "platform-team"},
        "confidence": 0.0,
        "top_3_neighbors": candidates["top_3_neighbors"],
        "consensus_score": 0.0,
        "blast_radius_check": "escalated",
        "evidence": {
            "reason": "Initial CLI smoke test",
            "trigger_service": query["trigger_service"],
        },
    }
```

`utils.py` tam thoi de trong.

## 9. Chay smoke test

Dam bao terminal dang o thu muc `submission` va `.venv` da kich hoat:

```powershell
python engine.py decide `
  --incident ../data-pack/eval/E01.json `
  --history ../data-pack/incidents_history.json `
  --actions ../data-pack/actions.yaml
```

Ket qua mong doi co dang:

```json
{
  "selected_action": "page_oncall",
  "params": {
    "team": "platform-team"
  },
  "confidence": 0.0,
  "incident_id": "E01"
}
```

Kiem tra audit:

```powershell
Get-Content audit.jsonl
```

Neu co mot dong JSON hop le, pipeline setup da thanh cong.

## 10. Chay grader lan dau

```powershell
python ../data-pack/grade.py `
  --audit audit.jsonl `
  --expected ../data-pack/eval/expected.json
```

Lan dau se sai nhieu incident vi thuat toan chua duoc viet. Day khong phai loi
setup. Muc dich chi la xac nhan grader doc duoc `audit.jsonl`.

## 11. Thu tu cai dat thuat toan

Khong viet tat ca cung luc. Lam theo thu tu:

### Giai doan 1: `features.py`

1. Normalize raw log.
2. Chi uu tien log `WARN` va `ERROR`.
3. Tinh error rate va latency tren tung trace edge.
4. Chia metric theo 30% dau va 30% cuoi de tinh ratio.
5. Tong hop affected services.
6. Du doan suspected root service tu nhieu signal.

Output nen co dang:

```python
{
    "log_text": "...",
    "affected_services": {"payment-svc", "checkout-svc"},
    "trace_edges": {},
    "metric_changes": {},
    "suspected_root_service": "payment-svc",
}
```

### Giai doan 2: `retrieval.py`

Tinh similarity:

```text
0.45 * log_similarity
+ 0.30 * trace_similarity
+ 0.15 * service_similarity
+ 0.10 * metric_similarity
```

Sau do:

1. Sap xep 29 incident lich su.
2. Lay top 3.
3. Bo neighbor co similarity qua thap.
4. Parse cac action lich su.
5. Vote theo similarity va outcome.

Outcome weight goi y:

```python
OUTCOME_WEIGHT = {
    "success": 1.0,
    "partial": 0.45,
    "failed": -0.75,
}
```

### Giai doan 3: `decision.py`

1. Chon candidate co vote tot nhat.
2. Tinh confidence tu best similarity va consensus.
3. Tru cost, downtime va blast radius.
4. Page neu incident la OOD.
5. Page neu confidence khong du cho action co blast radius lon.

Khong cho `page_oncall` tham gia utility nhu action binh thuong, vi cost cua no
bang 0 va no se luon thang.

## 12. Chay du 8 incident

Truoc moi lan chay full, xoa audit cu:

```powershell
Clear-Content audit.jsonl
```

Chay:

```powershell
1..8 | ForEach-Object {
    $id = "E{0:D2}" -f $_
    python engine.py decide `
      --incident "../data-pack/eval/$id.json" `
      --history ../data-pack/incidents_history.json `
      --actions ../data-pack/actions.yaml
}
```

Kiem tra so dong:

```powershell
(Get-Content audit.jsonl).Count
```

Ket qua phai la `8`.

Chay grader:

```powershell
python ../data-pack/grade.py `
  --audit audit.jsonl `
  --expected ../data-pack/eval/expected.json
```

Muc tieu dau tien:

- Khong crash tren 8 incident.
- Khong thieu incident trong audit.
- Dat it nhat 5/8.
- Khong vi pham `must_not_action`.

## 13. Cac loi thuong gap

### `python` mo Microsoft Store

`.venv` chua duoc kich hoat. Chay:

```powershell
.\.venv\Scripts\Activate.ps1
```

### `uv` bao access denied cache

Dat cache trong project:

```powershell
$env:UV_CACHE_DIR="$PWD\.uv-cache"
```

Sau do chay lai lenh `uv`.

### `ModuleNotFoundError: yaml`

```powershell
uv pip install pyyaml
```

### Khong tim thay data file

Dam bao dang chay lenh tu `submission`, khong phai tu project root:

```powershell
Get-Location
```

### Audit co nhieu dong trung

Moi lan chay engine, no append them mot dong. Truoc khi chay full:

```powershell
Clear-Content audit.jsonl
```

### Grader bao missing E01

`incident_id` phai la ten file `E01`, khong phai
`E01-2026-06-10-001`. Trong `engine.py`, dung:

```python
result["incident_id"] = incident_path.stem
```

## 14. Checklist setup

- [ ] Dang o dung project.
- [ ] Da tao `submission`.
- [ ] Da tao va kich hoat `.venv`.
- [ ] `python --version` tra ve Python 3.12.
- [ ] Da cai `numpy`, `scikit-learn`, `pyyaml`.
- [ ] Da tao du cac file code.
- [ ] Smoke test E01 chay khong loi.
- [ ] `audit.jsonl` co JSON hop le.
- [ ] Grader doc duoc audit.
- [ ] San sang cai dat `features.py`.

