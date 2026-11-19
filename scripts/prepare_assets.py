"""Prepare assets in a one-off container before the offline server starts."""
import argparse
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_model(verify=False):
    manifest = json.loads((ROOT / "model-manifest.json").read_text(encoding="utf-8"))
    model = ROOT / "models" / manifest["filename"]
    if not model.is_file():
        raise RuntimeError("Place the separately obtained GGUF at models/" + manifest["filename"])
    with model.open("rb") as stream:
        if stream.read(4) != b"GGUF":
            raise RuntimeError("The model does not have a GGUF header")
    if model.stat().st_size != manifest["size_bytes"]:
        raise RuntimeError("Model size differs from model-manifest.json; verify the model source")
    if verify:
        with model.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else _sha256(stream)
        if digest != manifest["sha256_local_reference"]:
            raise RuntimeError("Model checksum differs from the local reference")
        print("Model SHA-256 matches the project's local reference.")
    print("GGUF file is present and its header/size match the manifest.")


def _sha256(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check existing assets without network access or inference")
    parser.add_argument("--verify-model", action="store_true", help="Also hash the GGUF against the local reference")
    args = parser.parse_args(argv)
    check_model(args.verify_model)
    os.environ["HF_HUB_OFFLINE"] = "1" if args.check else "0"
    os.environ["HF_DATASETS_OFFLINE"] = "1" if args.check else "0"
    from src.config import EMBEDDING_MODEL_NAME, HF_DATASETS
    if args.check:
        from huggingface_hub import snapshot_download
        snapshot_download(EMBEDDING_MODEL_NAME, local_files_only=True)
        print("Embedding snapshot is cached (inference is checked during server warmup).")
        missing = [name for name in HF_DATASETS
                   if not list((ROOT / "datasets").glob(name.replace("/", "_") + "_*.csv"))]
        if len(missing) == len(HF_DATASETS):
            raise RuntimeError("No dataset CSVs found; run prepare_assets without --check first")
        if missing:
            print("Some dataset sources are absent: " + ", ".join(missing))
        print("Local dataset CSVs are available.")
    else:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(EMBEDDING_MODEL_NAME, device="cpu")
        if len(model.encode("")) != 1024:
            raise RuntimeError("Unexpected embedding dimension")
        del model
        print("Embedding model downloaded and checked.")
        from src.database import _load_dataset_prompts
        prompts, summary = _load_dataset_prompts()
        print(f"Prepared {len(prompts)} unique attack records for first-run ingestion.")
        if summary["failed_datasets"]:
            print("Unavailable sources: " + ", ".join(summary["failed_datasets"]))
    print("Assets ready. Start the server with docker compose up -d.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        raise SystemExit("ASSET PREPARATION FAILED: " + str(exc))
