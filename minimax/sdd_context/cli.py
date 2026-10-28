from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from .common import inside, load_task, read_json, write_json
from .index import SourceIndex
from .state import TaskState, build_context


@contextmanager
def module_lock(directory):
    import fcntl

    Path(directory).mkdir(parents=True, exist_ok=True)
    with (Path(directory) / "module.lock").open("w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def main():
    parser = argparse.ArgumentParser(description="SDD engineering context module")
    parser.add_argument("--task", default="sample/task.json", help="task JSON path; its parent is the project root")
    parser.add_argument("--state-dir", default=os.environ.get("SDD_STATE_DIR", ".sdd"))
    parser.add_argument("--output", help="write machine-readable JSON here")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("ingest")
    context_parser = commands.add_parser("context")
    context_parser.add_argument("--max-bytes", type=int, default=64000)
    checkpoint_parser = commands.add_parser("checkpoint")
    checkpoint_parser.add_argument("--done", action="append", default=[])
    checkpoint_parser.add_argument("--todo", action="append")
    checkpoint_parser.add_argument("--note", default="")
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--label", default="validation")
    verify_parser.add_argument("--evidence-dir", default=".sdd/evidence")
    agent_parser = commands.add_parser("agent")
    agent_parser.add_argument("--env-file")
    agent_parser.add_argument("--evidence-dir", default=".sdd/minimax")
    args = parser.parse_args()
    task, root = load_task(args.task)
    state_dir = Path(args.state_dir).resolve()
    manifest = read_json(inside(root, task["manifest"]))
    memory = TaskState(state_dir, manifest.get("project_id", "default") + "/" + task["id"])
    with module_lock(state_dir):
        if args.command == "checkpoint":
            old = memory.read()["state"] or {"completed": [], "todo": task["initial_todo"]}
            completed = list(dict.fromkeys(old["completed"] + args.done))
            result = memory.save({**old, "completed": completed,
                                  "todo": args.todo if args.todo is not None else
                                  [x for x in old["todo"] if x not in completed], "note": args.note})
        else:
            with SourceIndex(state_dir) as index:
                if args.command == "ingest":
                    result = index.import_project(root, task["manifest"])
                else:
                    selected = index.select(root, task)
            if args.command in {"context", "agent"}:
                context = build_context(selected, task, memory.read(), getattr(args, "max_bytes", 64000))
                if args.command == "context":
                    result = context
                else:
                    from .agent import generate, load_env
                    load_env(args.env_file)
                    result = generate(context, root, args.evidence_dir, state_dir)
                    old = context["progress"]["resumed_checkpoint"] or {
                        "completed": [], "todo": task["initial_todo"]}
                    memory.save({**old, "completed": list(dict.fromkeys(old["completed"] +
                                ["retrieve-current-context", "implement-min-stock"])),
                                 "todo": ["run-target-and-regression-tests"],
                                 "minimax_response_id": result["response_id"]})
            elif args.command == "verify":
                from .validation import verify
                result = verify(root, task, selected["snapshot"], args.evidence_dir, args.label)
                old = memory.read()["state"] or {"completed": [], "todo": task["initial_todo"]}
                summary = {"snapshot": selected["snapshot"], "task_sha256": selected["task_sha256"],
                           "passed": result["passed"],
                           "label": args.label, "results": result["results"]}
                memory.save({**old, "verification": summary,
                             "completed": list(dict.fromkeys(old["completed"] +
                                (["run-target-and-regression-tests"] if result["passed"] else []))),
                             "todo": [x for x in old["todo"] if x not in
                                      {"run-target-and-regression-tests", "rerun-tests-for-current-sources"}]
                                     if result["passed"] else old["todo"]})
        if args.output:
            write_json(args.output, result)
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "verify" and not result["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)

