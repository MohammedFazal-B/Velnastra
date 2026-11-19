# Velnastra

**Velnastra - Intent Firewall** is a local purple-team assessment workspace. BGE embeddings and Qdrant retrieve related attack context; a Qwen GGUF model makes every final intent decision. Similarity scores and keyword rules do not determine verdicts.

The UI classifies prompts and scripts; it does not generate chatbot replies. Assessment, Activity and Analytics use real stored results. Independent tests are the default; Conversation retains up to six allowed user turns. The Motion control enables or pauses decorative animations.

## What is included

Application code, local web assets, tests, Docker configuration, safe environment template, model fingerprint and diagnostic scripts. Credentials, GGUF weights, datasets, caches, saved prompts, learned vectors and screenshots are excluded.

~~~text
src/                       Python application
  web/                     Local HTML, CSS, JavaScript and logo
scripts/                   Asset setup, sharing audit and benchmarks
tests/                     Unit tests and synthetic regression fixtures
model-manifest.json        Local model fingerprint; no model weights
Dockerfile                 NVIDIA CUDA application image
docker-compose.yml         Loopback-only app and Qdrant services
.env.example               Safe template; copy to .env
README.md                  Setup and sharing guide
~~~

## Friend's laptop: Linux with NVIDIA GPU

Prerequisites:

1. Git, Docker Engine and the Docker Compose plugin.
2. A supported NVIDIA driver and NVIDIA Container Toolkit configured for Docker. Follow the official instructions for your distribution:
   https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
3. Sufficient GPU/host memory for the 5.8 GB GGUF plus inference context and embeddings. Capacity depends on hardware and settings. Allow substantial extra disk space for the large image, build cache, models and datasets.
4. Internet access for the initial build and asset preparation.

Check GPU/container support:

~~~bash
nvidia-smi
docker --version
docker compose version
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu22.04 nvidia-smi
~~~

This CUDA configuration is not directly usable on an AMD-only, Intel-only or macOS laptop.

### 1. Fork and clone

On the owner's GitHub repository, select **Fork > Create fork**, then clone your fork:

~~~bash
git clone https://github.com/FRIEND_USERNAME/Multi-Layer-Rag-Model.git
cd Multi-Layer-Rag-Model
git remote add upstream https://github.com/MohammedFazal-B/Multi-Layer-Rag-Model.git
~~~

Replace FRIEND_USERNAME with your account. The repository name is retained; the product displayed in the app is Velnastra.

For a private repository, the owner must grant access first. Private forking depends on account/repository policies. If Fork is unavailable, an invited collaborator can clone the owner's repository directly.

Official fork guide: https://docs.github.com/en/pull-requests/how-tos/work-with-forks/fork-a-repo

### 2. Create local configuration

~~~bash
cp .env.example .env
mkdir -p models data datasets hf_cache qdrant_storage
~~~

HF_TOKEN is intentionally blank. Public assets normally need no token. If a source requires authorization, use your own token and access permission. Never copy the owner's .env.

### 3. Obtain the GGUF separately

Place the authorized model here:

~~~text
models/Qwen3.5-9B.Q4_K_M.gguf
~~~

The model is not on GitHub. This checkout does not record a verified public download source or prove custom fine-tuning. Obtain it from an authorized source, or transfer the exact file separately if its license permits. Do not rename an unrelated model to this filename.

The local reference in model-manifest.json is:

~~~text
Size:   5780090592 bytes
SHA256: 4fffc67e7d22b6337150aaf3948f5d2d1789cb4b375a5fac83adcde7a19076c2
~~~

Compare after transfer:

~~~bash
sha256sum models/Qwen3.5-9B.Q4_K_M.gguf
~~~

This detects a different/corrupted transfer. It is not a publisher signature or proof of training provenance.

### 4. Build and prepare assets

~~~bash
docker build -t prompt-firewall:gpu -f Dockerfile .
docker compose run --rm --no-deps firewall python -m scripts.prepare_assets
~~~

Preparation downloads/checks BGE and prepares dataset CSVs. It enables network access in that one-off process, without running HTTP, ingesting Qdrant or copying anyone's saved assessments.

Some dataset sources may be unavailable or require authorization. The helper reports unavailable sources and fails if no usable attack data is obtained. Dataset/model licenses remain separate; do not publish cached assets by default.

Check prepared assets without network access:

~~~bash
docker compose run --rm --no-deps firewall python -m scripts.prepare_assets --check --verify-model
~~~

### 5. Start

~~~bash
docker compose up -d
docker logs -f firewall-app
~~~

First startup ingests vectors and warms the models before HTTP is ready. Later starts reuse the completed index. Wait for the HTTP server message, then open http://localhost:8000/.

Ctrl+C exits the log viewer; containers keep running.

~~~bash
curl http://localhost:8000/health
~~~

Ready means status=ok, cognitive_layer=true and vector_store=ready. Host ports bind only to 127.0.0.1.

### 6. Test and stop

~~~bash
docker exec firewall-app python -m pytest tests -q -p no:cacheprovider
docker exec firewall-app python -m pip check
docker exec firewall-app python -u -m scripts.benchmark_intent
docker compose down
~~~

The live benchmark adds classification records and learned contexts to the local stores. Unit tests use mocks for routing/storage invariants; they are not a measurement of detection accuracy.

To sync a fork later, use GitHub's Sync fork control or fetch upstream and merge after reviewing your own changes.

## Push this working copy to GitHub from Windows

This folder already has Git history, branch main and this origin:

https://github.com/MohammedFazal-B/Multi-Layer-Rag-Model.git

No Git reinitialization, remote creation or force push is required for that repository.

1. Sign into GitHub and verify that the repository belongs to your account. Select the intended visibility. For private sharing, invite the friend through Settings > Collaborators.
2. Open PowerShell in this project and run the sharing check.
3. Stage and review the exact diff. It includes the earlier cleanup/deletions.
4. Commit and push after review.

~~~powershell
Set-Location "C:\Users\moham\Documents\my-qdrant-project"
python -B -m scripts.check_sharing
git status --short
git add .gitignore .dockerignore .gitattributes .env.example Dockerfile docker-compose.yml requirements.txt README.md model-manifest.json src scripts tests
git add -u
git diff --cached --stat
git diff --cached --check
git diff --cached
python -B -m scripts.check_sharing
git commit -m "Prepare Velnastra for local setup and collaboration"
git push -u origin main
~~~

If the audit fails, resolve the locations before pushing. It reports locations/types without printing secrets, and checks both current candidates and existing history. Pattern scanning cannot guarantee every possible secret is absent.

Authenticate using Git Credential Manager/browser sign-in or SSH. Do not put tokens/passwords in URLs or source. If the remote has newer commits, fetch and review the divergence; do not force push or blindly overwrite it.

For a different repository, first create an empty GitHub repository without a generated README, license or .gitignore, then deliberately change origin. The commands above use the existing verified origin.

Official push guide: https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github

## Protected from upload

| Excluded | Reason |
| --- | --- |
| .env and .env.* except .env.example | Credentials and local configuration |
| Keys/certificates, .aws/, .ssh/ | Private credentials |
| data/ and SQLite files/journals | Saved prompts, analytics and local reports |
| qdrant_storage/ | Learned prompts and private vector state |
| models/ and model weights | Large assets, provenance and redistribution requirements |
| datasets/ and hf_cache/ | Downloaded data and model assets |
| Logs, archives, screenshots, editor/agent settings | Generated or machine-specific information |

Docker build context allows only source, scripts, tests, requirements, Dockerfile and the non-secret model manifest, with additional secret exclusions.

.gitignore does not remove secrets from existing history. If a real credential is ever found, revoke/rotate it and clean history before sharing. Do not upload the entire project as a ZIP; that can bypass these protections.

The owner should choose a project-wide license before broader redistribution. React Bits-derived UI effects retain their upstream attribution/license in src/web/react-bits-LICENSE.txt. Model and dataset licenses are separate.

## Configuration and troubleshooting

| Variable | Purpose |
| --- | --- |
| LLM_GPU_LAYERS=-1 | Full GPU offload; lower if VRAM is insufficient |
| LLM_CONTEXT_SIZE=8192 | Model context; lower to reduce memory demand and accepted context |
| FORCE_REBUILD=false | Reuse index; true deletes/rebuilds the collection |
| ENABLE_DATASET_UPDATES=false in template | Scheduled network refresh disabled by default |
| DATASET_UPDATE_TIMEZONE=Asia/Kolkata | Optional weekly refresh timezone |
| MAX_PENDING_EVALUATIONS=3 | Bounded inference queue |

Serving uses HF_HUB_OFFLINE=1 after preparation. Missing model/cache causes startup failure rather than fake safe verdicts. After editing .env, recreate with docker compose up -d. Speed depends on hardware.

Keep data/ingestion_state.json with Qdrant state. It marks completed ingestion; interrupted builds resume with deterministic IDs. Do not copy private vectors or analytics to the friend.

POST /check accepts JSON with a nonempty prompt and optional history (up to six user/assistant turns), limited to 64 KiB. Oversized model context returns 413; full queue returns 429. /api/activity supplies 30 recent records, /api/analytics aggregates outcomes, and /health reports readiness. /v1/models is not implemented.

Every verdict uses Qwen, including previously learned attacks. Malicious input/history is retained under a deterministic ID; exact repeats report already_known. Missing/paraphrased evidence falls back to the classified input for learning. Learning errors remain visible without changing a BLOCK verdict.

Stop the HTTP service before loading a second Qwen instance for CLI diagnostics:

~~~bash
docker compose stop firewall
docker compose run --rm --no-deps firewall python -m src.main --cli
docker compose up -d firewall
~~~

## Validation limits

Tests cover the configured local model, routing and learning paths. They do not establish perfect detection, held-out accuracy or compatibility with every laptop. Ingestion combines dataset train/test splits, so those same records are not an independent evaluation.

This is a local assessment tool, not an authenticated production gateway. Supplied history is not a trusted server-side session. Saved prompts and contexts are plaintext locally. Keep services on loopback and do not publish private stores or credentials.

Fresh internet downloads and a clean image build must still succeed on the friend's network/hardware. Local cached-asset verification is not a substitute for that first installation.
