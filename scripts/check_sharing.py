"""Read-only pre-push checks. Prints locations/types, never secret values."""
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "provider token": rb"(?:hf_|ghp_|github_pat_|sk-proj-|sk-)[A-Za-z0-9_-]{20,}",
    "AWS access key": rb"(?:AKIA|ASIA)[A-Z0-9]{16}",
    "private key": rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    "credential URL": rb"https?://[^/\\s:@]+:[^/\\s@]+@",
}
PRIVATE_DIRS = {"data", "models", "datasets", "hf_cache", "qdrant_storage",
                "venv", ".venv", ".aws", ".ssh", ".codex", ".agents"}
LIMIT = 50 * 1024 * 1024


def git(*args, input=None, check=True):
    result = subprocess.run(["git", *args], cwd=ROOT, input=input,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode:
        raise RuntimeError("Git command failed: " + " ".join(args))
    return result


def private_path(name):
    path = PurePosixPath(name)
    return (bool(set(path.parts) & PRIVATE_DIRS)
            or (path.name.startswith(".env") and path.name != ".env.example")
            or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".gguf", ".safetensors", ".db"})


def findings(data):
    return [name for name, pattern in PATTERNS.items() if re.search(pattern, data)]


def main():
    problems = []
    names = git("ls-files", "--cached", "--others", "--exclude-standard", "-z").stdout.decode().split("\0")
    scanned = 0
    for name in sorted(set(filter(None, names))):
        path = ROOT / name
        if not path.is_file():
            continue
        if private_path(name):
            problems.append({"scope": "working tree", "path": name, "type": "private file would be published"})
        size = path.stat().st_size
        if size > LIMIT:
            problems.append({"scope": "working tree", "path": name, "type": "file exceeds 50 MiB"})
            continue
        scanned += 1
        for kind in findings(path.read_bytes()):
            problems.append({"scope": "working tree", "path": name, "type": kind})
    checked = set()
    commits = git("rev-list", "--all").stdout.decode().splitlines()
    for commit in commits:
        entries = git("ls-tree", "-rz", commit).stdout.split(b"\0")
        for entry in filter(None, entries):
            meta, raw_name = entry.split(b"\t", 1)
            mode, kind, sha = meta.decode().split()
            if kind != "blob":
                continue
            name = raw_name.decode()
            if private_path(name):
                problems.append({"scope": commit[:12], "path": name, "type": "private file in history"})
            if sha in checked:
                continue
            checked.add(sha)
            size = int(git("cat-file", "-s", sha).stdout)
            if size > LIMIT:
                problems.append({"scope": commit[:12], "path": name, "type": "historical blob exceeds 50 MiB"})
                continue
            for match in findings(git("cat-file", "blob", sha).stdout):
                problems.append({"scope": commit[:12], "path": name, "type": match})
    probes = [".env", ".env.production", ".env.backup", "models/probe.gguf",
              "data/probe.db-wal", "datasets/probe.csv", "hf_cache/probe",
              "qdrant_storage/probe", "private.key", "debug.log", ".aws/credentials"]
    for probe in probes:
        if git("check-ignore", "--no-index", probe, check=False).returncode != 0:
            problems.append({"scope": "ignore rules", "path": probe, "type": "not excluded"})
    report = {"passed": not problems, "scanned_files": scanned, "commits_checked": len(commits),
              "problems": problems, "limit": "Pattern checks do not guarantee absence of every possible secret."}
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "sharing_readiness.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as exc:
        sys.exit(str(exc))
