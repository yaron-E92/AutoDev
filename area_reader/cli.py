from __future__ import annotations

import argparse
import os
from pathlib import Path

from area_reader.settings import (
    DEFAULT_CODER_NUM_PREDICT,
    DEFAULT_MAX_CHARS_PER_AREA,
    DEFAULT_READER_NUM_PREDICT,
    DEFAULT_SYNTH_NUM_PREDICT,
)

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Run an area-based local Ollama reader-synthesis-coder benchmark."
    )
    parser.add_argument("--repo", required=True, help="Repository to read for context.")
    parser.add_argument("--reader", help="Deprecated alias for --reader-model.")
    parser.add_argument("--reader-model", help="Area reader model name.")
    parser.add_argument(
        "--reader-provider",
        choices=("command", "chat-completions", "openai-compatible", "mock"),
        default="command",
        help="Reader provider transport.",
    )
    parser.add_argument("--reader-command", default="", help="Command provider command for reader prompts.")
    parser.add_argument("--reader-base-url", default="", help="chat-completions base URL for reader prompts.")
    parser.add_argument("--reader-api-key-env", default="", help="Environment variable containing reader API key.")
    parser.add_argument("--reader-timeout-seconds", type=int, default=600)
    parser.add_argument(
        "--synthesizer",
        help="Ollama synthesis reader model name. Defaults to --reader.",
    )
    parser.add_argument("--coder", help="Deprecated alias for --coder-model.")
    parser.add_argument("--coder-model", help="Coder model name.")
    parser.add_argument(
        "--coder-provider",
        choices=("command", "chat-completions", "openai-compatible", "mock"),
        default="command",
        help="Coder provider transport.",
    )
    parser.add_argument("--coder-command", default="", help="Command provider command for coder prompts.")
    parser.add_argument("--coder-base-url", default="", help="chat-completions base URL for coder prompts.")
    parser.add_argument("--coder-api-key-env", default="", help="Environment variable containing coder API key.")
    parser.add_argument("--coder-timeout-seconds", type=int, default=600)
    parser.add_argument("--issue", required=True, help="Issue or task text.")
    parser.add_argument(
        "--areas",
        default="auto",
        help="Area routing: auto, all, or comma-list such as backend,web,maui,ci.",
    )
    parser.add_argument(
        "--max-chars-per-area",
        type=int,
        default=DEFAULT_MAX_CHARS_PER_AREA,
        help=f"Maximum input bundle characters per area. Default: {DEFAULT_MAX_CHARS_PER_AREA}.",
    )
    parser.add_argument(
        "--reader-num-predict",
        type=int,
        default=DEFAULT_READER_NUM_PREDICT,
        help=f"Area reader num_predict option. Default: {DEFAULT_READER_NUM_PREDICT}.",
    )
    parser.add_argument(
        "--synth-num-predict",
        type=int,
        default=DEFAULT_SYNTH_NUM_PREDICT,
        help=f"Synthesis reader num_predict option. Default: {DEFAULT_SYNTH_NUM_PREDICT}.",
    )
    parser.add_argument(
        "--coder-num-predict",
        type=int,
        default=DEFAULT_CODER_NUM_PREDICT,
        help=f"Coder num_predict option. Default: {DEFAULT_CODER_NUM_PREDICT}.",
    )
    parser.add_argument("--out", required=True, help="Output directory for benchmark files.")
    return parser.parse_args(argv)

def expand_user_path(value):
    return Path(os.path.expanduser(value)).resolve()
