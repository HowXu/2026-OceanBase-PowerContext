"""Build a reproducible submission archive, excluding credentials and runtime data."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
TOP_LEVEL = {".dockerignore", ".env.example", ".gitattributes", ".gitignore", "Dockerfile", "README.md",
             "compose.yaml", "nginx.conf", "requirements.txt", "requirements-dev.txt", "station7.yml"}
REQUIRED = {"README.md", "station7.yml", "requirements.txt", "compose.yaml", "Dockerfile", ".env.example",
            "station7/app.py", "station7/memory.py", "scripts/run_powercontext.py", "scripts/verify_dify.py"}
FOLDERS = {"station7": {".py"}, "scripts": {".py"}, "tests": {".py"},
           "static": {".html", ".css", ".js", ".svg", ".png", ".jpg", ".webp"},
           "evidence": {".md", ".json", ".txt", ".png", ".jpg", ".webp"}}
EXCLUDED = {"__pycache__", ".pytest_cache", ".venv", "data", "dist", "node_modules", ".git"}
BINARY = {".png", ".jpg", ".webp"}


def build_archive(root: Path, output: Path):
    paths = [root / name for name in TOP_LEVEL if (root / name).is_file()]
    for folder, suffixes in FOLDERS.items():
        paths.extend(path for path in (root / folder).rglob("*")
                     if path.is_file() and path.suffix.lower() in suffixes
                     and not EXCLUDED.intersection(path.relative_to(root).parts))
    paths = sorted(paths, key=lambda path: path.relative_to(root).as_posix())
    relative_paths = {path.relative_to(root).as_posix() for path in paths}
    missing = REQUIRED - relative_paths
    if missing:
        raise ValueError("Required submission files missing: " + ", ".join(sorted(missing)))
    values = dotenv_values(root / ".env")
    secrets = {name: value.encode() for name, value in values.items()
               if value and name.endswith(("TOKEN", "API_KEY", "PASSWORD", "SECRET_KEY"))}
    files = []
    for path in paths:
        payload = path.read_bytes() if path.suffix.lower() in BINARY else path.read_text(encoding="utf-8").encode()
        for name, secret in secrets.items():
            if secret in payload:
                raise ValueError(f"Refusing to package credential {name} found in {path.relative_to(root)}")
        files.append(("dify/" + path.relative_to(root).as_posix(), payload))
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload)
    manifest = {"archive": output.name, "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "files": [{"path": name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
                          for name, payload in files]}
    output.with_suffix(".manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "station7-submission.zip")
    parser.add_argument("--require-ai", action="store_true", help="Refuse to package until actual AI acceptance is complete")
    args = parser.parse_args()
    if args.require_ai:
        from check_submission import check_submission
        problems = check_submission(ROOT)
        if problems:
            raise SystemExit("Submission is not ready: " + "; ".join(problems))
    manifest = build_archive(ROOT, args.output.resolve())
    print(f"Packaged {len(manifest['files'])} files; credentials/runtime data excluded")
    print(args.output.resolve())
    print("SHA256:", manifest["sha256"])
