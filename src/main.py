import os
import sys
from src.database import setup_database_and_embed
from src.llm import setup_llm, warm_llm
from src.server import run_server
from src.firewall_core import check_user_input

def print_check_result(prompt, result):
    print(f"\nUser Input: '{prompt}'")
    if result["status"] == "BLOCK":
        matched = (result.get("matched_against") or "")[:100]
        print(f"ACTION: {result['status']} (Score: {result['score']})")
        if matched:
            print(f"   Matched threat: '{matched}...'")
    else:
        print(f"ACTION: {result['status']} (Score: {result.get('score', 0)})")
        if result.get("reason"):
            print(f"   {result['reason']}")


def run_cli(model, client, llm):
    print("\n" + "=" * 50)
    print("RAG prompt firewall (interactive)")
    print("=" * 50)
    print("Qdrant retrieval context -> Qwen intent classification")
    print("Type a prompt and press Enter. Empty line, quit, or exit to stop.\n")

    history = []

    while True:
        try:
            prompt = input("Prompt> ")
        except (EOFError, KeyboardInterrupt):
            print("\nStopping.")
            break
        if prompt.strip().lower() in {"", "quit", "exit"}:
            break

        result = check_user_input(prompt, model, client, llm, history=history)
        print_check_result(prompt, result)

        if result["status"] == "ALLOW":
            history.append({"role": "user", "content": prompt})
            history = history[-MAX_HISTORY_TURNS:]
            print(f"(Context updated: {len(history)} turns in memory)")


from apscheduler.schedulers.background import BackgroundScheduler
from src.database import update_datasets_job, _embedding_dimension_worker
from src.config import MAX_HISTORY_TURNS, DATASET_UPDATE_TIMEZONE, ENABLE_DATASET_UPDATES


def main():
    model = client = llm = scheduler = None
    try:
        if "--update-datasets" in sys.argv:
            update_datasets_job()
            return
        model, client = setup_database_and_embed()
        llm = setup_llm()
        if llm is None:
            raise RuntimeError("Qwen could not load; the classifier will not accept requests")
        embedding_warmup = model.submit(_embedding_dimension_worker)
        warm_llm(llm)
        dimension = embedding_warmup.result(timeout=180)
        if dimension != 1024:
            raise RuntimeError(f"Unexpected embedding dimension: {dimension}")
        print("Embedding model is ready before accepting requests.")
        if ENABLE_DATASET_UPDATES:
            scheduler = BackgroundScheduler(timezone=DATASET_UPDATE_TIMEZONE)
            scheduler.add_job(update_datasets_job, "cron", day_of_week="sun", hour=3,
                              max_instances=1, coalesce=True, misfire_grace_time=3600)
            scheduler.start()
            print(f"Dataset updater scheduled: Sunday 03:00 {DATASET_UPDATE_TIMEZONE}.")
        mode = "cli" if "--cli" in sys.argv else os.getenv("FIREWALL_MODE", "server")
        if mode == "cli":
            run_cli(model, client, llm)
        else:
            run_server(model, client, llm)
    finally:
        if scheduler is not None and scheduler.running:
            scheduler.shutdown(wait=False)
        if model is not None:
            model.shutdown(wait=True, cancel_futures=True)
        if llm is not None:
            llm.close()
        if client is not None:
            client.close()


if __name__ == "__main__":
    import signal
    def stop_requested(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop_requested)
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
