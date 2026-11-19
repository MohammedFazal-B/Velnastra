import os
from llama_cpp import Llama

# Always resolve relative to this file's location → project_root/models/
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODEL_PATH = os.path.join(_PROJECT_ROOT, "models", "Qwen3.5-9B.Q4_K_M.gguf")

def setup_llm():
    print("\nInitializing Qwen Cognitive Defense Layer...")
    if not os.path.exists(_MODEL_PATH):
        print(f"Model file not found at: {_MODEL_PATH}")
        print("Cognitive Defense Layer will be disabled. Place the GGUF file in the models/ folder.")
        return None
    try:
        llm = Llama(
            model_path=_MODEL_PATH,
            n_ctx=int(os.getenv("LLM_CONTEXT_SIZE", "8192")),
            n_gpu_layers=int(os.getenv("LLM_GPU_LAYERS", "-1")),  # Embeddings run on CPU after ingestion, leaving VRAM for Qwen
            verbose=False
        )
        print(f"Qwen model loaded successfully from: {_MODEL_PATH}")
        return llm
    except Exception as e:
        print(f"Failed to load Qwen model: {e}")
        print("Cognitive Defense Layer will be disabled.")
        return None


def warm_llm(llm):
    """Prime inference without a labeled example, classification or stored signature."""
    from src.firewall_core import _build_classifier_prompt
    llm(_build_classifier_prompt("", [], []), max_tokens=1,
        temperature=0.0, stop=["<|im_end|>"], echo=False)
