# =============================================================================
# CONFIGURATION MODULE
# Python Refactoring Agent
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


@dataclass
class Config:
    """Configuration for the Python refactoring agent."""

    # ----- Ollama settings -----
    ollama_model: str = "deepseek-coder"
    ollama_base_url: str = "http://localhost:11434"

    # ----- LLM temperatures (lower = more deterministic, important for code) -----
    temperature_planner: float = 0.0
    temperature_drafters: float = 0.1
    temperature_aggregator: float = 0.0

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
