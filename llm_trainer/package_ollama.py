from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Create Ollama Modelfile for LoRA/QLoRA adapter")
    parser.add_argument("--adapter-dir", required=True)
    parser.add_argument("--output-dir", default="/workspace/artifacts/ollama")
    parser.add_argument("--base", default="mistral:7b-instruct-q4_K_M")
    parser.add_argument("--model-name", default="binfin-mistral-finance")
    args = parser.parse_args()

    adapter_dir = Path(args.adapter_dir).resolve()
    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    modelfile = out_dir / "Modelfile"
    modelfile.write_text(
        "\n".join(
            [
                f"FROM {args.base}",
                f"ADAPTER {adapter_dir.as_posix()}",
                "PARAMETER temperature 0.2",
                "SYSTEM You are BINFIN's fine-tuned crypto market analyst for finance news sentiment and signal context.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"Created {modelfile}")
    print("Build command inside ollama container:")
    print(f"ollama create {args.model_name} -f {modelfile.as_posix()}")


if __name__ == "__main__":
    main()
