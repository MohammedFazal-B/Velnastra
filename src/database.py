import os
import concurrent.futures
import multiprocessing
import json
import hashlib
import uuid
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams, PointStruct
from src.config import EMBEDDING_MODEL_NAME, QDRANT_URL, COLLECTION_NAME, FORCE_REBUILD, HF_DATASETS, DATA_DIR, PROJECT_ROOT

_INGESTION_STATE = DATA_DIR / "ingestion_state.json"

_worker_model = None

def _init_worker(device=None):
    global _worker_model
    import torch
    from sentence_transformers import SentenceTransformer
    device = device or os.getenv("EMBEDDING_DEVICE", "cpu")
    print(f"Embedding worker initialized on device: {device}")
    _worker_model = SentenceTransformer(
        EMBEDDING_MODEL_NAME, device=device,
        model_kwargs={"torch_dtype": torch.float16 if device.startswith("cuda") else torch.float32}
    )

def _encode_text_worker(text):
    global _worker_model
    if _worker_model is None:
        _init_worker()
    return _worker_model.encode(text).tolist()

def _encode_batch_worker(texts):
    global _worker_model
    if _worker_model is None:
        _init_worker()
    return _worker_model.encode(texts).tolist()

def extract_bad_prompts_from_csv(dataset_name, df):
    """Filters the locally saved CSVs based on their specific label columns."""
    bad_prompts = []
    text_col = next((col for col in ['text', 'prompt', 'user_input', 'Goal'] if col in df.columns), None)
    if not text_col:
        return bad_prompts

    if "deepset" in dataset_name or "safe-guard" in dataset_name:
        if 'label' in df.columns:
            bad_prompts = df[df['label'] == 1][text_col].tolist()

    elif "neuralchemy" in dataset_name:
        label_col = 'binary_label' if 'binary_label' in df.columns else 'label'
        if label_col in df.columns:
            bad_prompts = df[df[label_col] == 1][text_col].tolist()

    elif "wildguardmix" in dataset_name:
        if 'prompt_harm_label' in df.columns or 'adversarial' in df.columns:
            bad = df[(df.get('prompt_harm_label') == 'harmful') | (df.get('adversarial') == True)]
            bad_prompts = bad[text_col].tolist()

    elif "toxic-chat" in dataset_name:
        if 'toxicity' in df.columns or 'jailbreaking' in df.columns:
            bad = df[(df.get('toxicity') == 1) | (df.get('jailbreaking') == 1)]
            bad_prompts = bad[text_col].tolist()

    elif "JBB-Behaviors" in dataset_name:
        if 'Category' in df.columns:
            bad = df[df['Category'] != 'Benign']
            bad_prompts = bad[text_col].tolist()

    elif "BeaverTails" in dataset_name:
        if 'is_safe' in df.columns:
            bad = df[df['is_safe'] == False]
            bad_prompts = bad[text_col].tolist()

    # Filter out noise: very short prompts are likely mislabeled (like "hi", "hey whats up")
    valid_prompts = []
    for p in bad_prompts:
        p_str = str(p).strip()
        if p_str and len(p_str.split()) >= 4 and len(p_str) > 15:
            valid_prompts.append(p_str)

    return valid_prompts



def _embedding_dimension_worker():
    if _worker_model is None:
        _init_worker()
    return len(_worker_model.encode(""))


def _make_embedding_executor(device):
    return concurrent.futures.ProcessPoolExecutor(
        max_workers=1, mp_context=multiprocessing.get_context("spawn"),
        initializer=_init_worker, initargs=(device,),
    )


def _read_ingestion_state():
    try:
        state = json.loads(_INGESTION_STATE.read_text(encoding="utf-8"))
        return isinstance(state, dict) and state.get("complete") is True
    except (OSError, ValueError, TypeError):
        return False


def _write_ingestion_state(complete, prompts=None):
    state = {"complete": complete}
    if prompts is not None:
        state["prompts"] = prompts
    _INGESTION_STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = _INGESTION_STATE.with_name(
        _INGESTION_STATE.name + "." + uuid.uuid4().hex + ".tmp"
    )
    try:
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(_INGESTION_STATE)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_csv(frame, destination):
    temporary = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        frame.to_csv(temporary, index=False)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _load_dataset_prompts(refresh=False):
    from datasets import load_dataset
    import pandas as pd
    dataset_dir = PROJECT_ROOT / "datasets"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    prompts, refreshed, failures = set(), 0, []
    for dataset_name, config in HF_DATASETS.items():
        safe_name = dataset_name.replace("/", "_")
        files = list(dataset_dir.glob(safe_name + "_*.csv"))
        if refresh or not files:
            try:
                options = {"token": os.getenv("HF_TOKEN") or None}
                dataset = (load_dataset(dataset_name, config, **options) if config
                           else load_dataset(dataset_name, **options))
                for split in dataset:
                    if "JBB-Behaviors" in dataset_name and split == "benign":
                        continue
                    _atomic_csv(dataset[split].to_pandas(),
                                dataset_dir / f"{safe_name}_{split}.csv")
                refreshed += 1
            except Exception as exc:
                failures.append(dataset_name)
                print(f"[Dataset] Could not refresh {dataset_name}: {exc}")
            files = list(dataset_dir.glob(safe_name + "_*.csv"))
        for csv in files:
            if "JBB-Behaviors" in dataset_name and csv.name.endswith("_benign.csv"):
                continue
            prompts.update(extract_bad_prompts_from_csv(dataset_name, pd.read_csv(csv)))
    if refresh and not refreshed:
        raise RuntimeError("No datasets refreshed; existing vectors were preserved")
    if not prompts:
        raise RuntimeError("No malicious prompts were loaded from datasets")
    ordered = sorted(prompts)
    _atomic_csv(pd.DataFrame({"malicious_prompt": ordered}),
                dataset_dir / "_ALL_MALICIOUS_PROMPTS_MASTER.csv")
    return ordered, {"datasets_refreshed": refreshed, "failed_datasets": failures}


def _ensure_collection(client):
    if not client.collection_exists(COLLECTION_NAME):
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
        )


def _upsert_dataset_prompts(prompts, client, executor):
    batch_size = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))
    if batch_size < 1:
        raise ValueError("EMBEDDING_BATCH_SIZE must be positive")
    uploaded = 0
    for i in range(0, len(prompts), batch_size):
        texts = prompts[i:i + batch_size]
        ids = [int(hashlib.md5(t.encode("utf-8")).hexdigest(), 16) % (2**63) for t in texts]
        known = {p.id for p in client.retrieve(
            COLLECTION_NAME, ids=ids, with_payload=False, with_vectors=False
        )}
        pending = [(pid, text) for pid, text in zip(ids, texts) if pid not in known]
        if not pending:
            continue
        vectors = executor.submit(_encode_batch_worker, [t for _, t in pending]).result()
        if len(vectors) != len(pending):
            raise ValueError("Dataset embedding count does not match pending signatures")
        client.upsert(collection_name=COLLECTION_NAME, wait=True, points=[
            PointStruct(id=pid, vector=vector, payload={"text":text, "source":"huggingface_dataset"})
            for (pid, text), vector in zip(pending, vectors)
        ])
        uploaded += len(pending)
        print(f"[Ingestion] Processed {i + len(texts)}/{len(prompts)}; added {uploaded}")
    return uploaded


def setup_database_and_embed():
    """Reuse a completed index or safely resume without losing learned vectors."""
    client, executor = None, None
    runtime_device = os.getenv("EMBEDDING_DEVICE", "cpu")
    try:
        client = QdrantClient(QDRANT_URL, timeout=10)
        # Compose starts Qdrant before the app, but the database can still be opening.
        import time
        for attempt in range(5):
            try:
                exists = client.collection_exists(COLLECTION_NAME)
                break
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(1)
        if exists and not FORCE_REBUILD and _read_ingestion_state():
            count = client.get_collection(COLLECTION_NAME).points_count
            if count > 0:
                print(f"Found completed collection '{COLLECTION_NAME}' with {count} vectors; reusing it.")
                return _make_embedding_executor(runtime_device), client
        if exists and FORCE_REBUILD:
            client.delete_collection(COLLECTION_NAME)
        _ensure_collection(client)
        _write_ingestion_state(False)
        prompts, _ = _load_dataset_prompts()
        ingestion_device = os.getenv("INGESTION_DEVICE", runtime_device)
        executor = _make_embedding_executor(ingestion_device)
        _upsert_dataset_prompts(prompts, client, executor)
        _write_ingestion_state(True, len(prompts))
        if ingestion_device != runtime_device:
            executor.shutdown(wait=True)
            executor = _make_embedding_executor(runtime_device)
        return executor, client
    except BaseException:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        if client is not None:
            client.close()
        raise


def _update_datasets_worker(url):
    """Online refresh runs only in a dedicated process, not in the API process."""
    os.environ["HF_HUB_OFFLINE"] = "0"
    os.environ["HF_DATASETS_OFFLINE"] = "0"
    import huggingface_hub.constants
    import datasets.config
    huggingface_hub.constants.HF_HUB_OFFLINE = False
    datasets.config.HF_DATASETS_OFFLINE = False
    client, executor = None, None
    try:
        prompts, summary = _load_dataset_prompts(refresh=True)
        client = QdrantClient(url, timeout=10)
        _ensure_collection(client)
        _write_ingestion_state(False)
        # Runtime CPU mode avoids competing with the serving Qwen GPU allocation.
        executor = _make_embedding_executor(os.getenv("EMBEDDING_DEVICE", "cpu"))
        summary["new_signatures"] = _upsert_dataset_prompts(prompts, client, executor)
        summary["dataset_signatures"] = len(prompts)
        _write_ingestion_state(True, len(prompts))
        print(f"[Dataset update] {summary}")
        return summary
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        if client is not None:
            client.close()


def update_datasets_job(_client=None):
    # Do not pickle an HTTP client or toggle offline globals in the serving process.
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=1, mp_context=multiprocessing.get_context("spawn")
    ) as updater:
        return updater.submit(_update_datasets_worker, QDRANT_URL).result()
