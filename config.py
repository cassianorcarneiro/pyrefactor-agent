# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#
# CONFIGURATION MODULE
# Python Refactoring Agent
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


@dataclass
class Config:
    """Configuration for the Python refactoring agent."""

    # ----- Ollama settings -----
    # A code-specialized model is strongly recommended. General chat models
    # are noticeably worse at preserving Python semantics during refactoring.
    ollama_model: str = "deepseek-coder"
    ollama_base_url: str = "http://127.0.0.1:11434"

    # Optional per-agent model overrides. Empty string means "use default".
    # Useful when you want a larger model only for the aggregator (the most
    # demanding step) while keeping fast models on the drafters.
    ollama_model_translator: str = ""
    ollama_model_refactorer: str = ""
    ollama_model_documenter: str = ""
    ollama_model_aggregator: str = ""

    # ----- LLM temperatures (lower = more deterministic, important for code) -----
    temperature_translator: float = 0.0
    temperature_refactorer: float = 0.1
    temperature_documenter: float = 0.1
    temperature_aggregator: float = 0.0

    # ----- Robustness -----
    # If the aggregator's output fails AST validation, retry the aggregator
    # this many times before falling back to a draft.
    aggregator_ast_retries: int = 1

    # ----- File handling -----
    output_suffix: str = "_refactored"
    report_suffix: str = "_report"
    supported_extensions: Tuple[str, ...] = (".py",)
    max_file_size_kb: int = 500  # Skip files larger than this

    # ----- Refactoring options -----
    translate_to_english: bool = True
    enforce_pep8: bool = True
    add_type_hints: bool = True
    add_docstrings: bool = True
    validate_syntax: bool = True  # AST validation of output
    preserve_logic: bool = True   # Critical: never alter behavior

    # ----- LLM context limits -----
    max_code_chars: int = 12000   # Truncate very long files for the LLM

    # ----- Versioned prompts -----
    prompts_dir: str = "./prompts"
