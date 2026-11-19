"""Load the real local GGUF and request one short classification."""
from src.llm import setup_llm
from src.firewall_core import _build_classifier_prompt, parse_llm_output_robust

if __name__ == "__main__":
    llm = setup_llm()
    if llm is None:
        raise SystemExit("MODEL LOAD FAILED")
    prompt = _build_classifier_prompt("What is the capital of France?", [], [])
    result = llm(prompt, max_tokens=160, temperature=0, stop=["<|im_end|>"])
    output = result["choices"][0]["text"]
    print("MODEL OUTPUT:", output, flush=True)
    parsed = parse_llm_output_robust(output)
    assert parsed["status"] == "SAFE", parsed
    print("MODEL SMOKE PASSED", flush=True)
