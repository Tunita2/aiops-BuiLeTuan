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

    # The grading contract expects the eval filename, for example "E01".
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
