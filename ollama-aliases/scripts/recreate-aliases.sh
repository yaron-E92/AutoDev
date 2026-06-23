#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODELS_DIR="$(cd "$SCRIPT_DIR/../modelfiles" && pwd)"

ollama create qwen35-9b-32k -f "$MODELS_DIR/qwen35-9b-32k.Modelfile"
ollama create qwen35-9b-64k -f "$MODELS_DIR/qwen35-9b-64k.Modelfile"
ollama create devstral-small2-12k -f "$MODELS_DIR/devstral-small2-12k.Modelfile"
ollama create devstral-small2-16k -f "$MODELS_DIR/devstral-small2-16k.Modelfile"

ollama list | grep -Ei 'qwen35|devstral|NAME'
