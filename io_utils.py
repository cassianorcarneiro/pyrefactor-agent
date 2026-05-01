# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#
# I/O utilities for the refactoring agent.
# Kept out of agent.py so they can be unit-tested independently.
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#

from __future__ import annotations

import ast
import re
from typing import Tuple


# ---------- Code fence handling ------------------------------------------------------------------

# Matches ```python\n...\n``` or ```py\n...\n``` or ```\n...\n```
_FENCE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)\n```", re.DOTALL)


def strip_code_fences(text: str) -> str:
    """Remove markdown code fences if the LLM wrapped the code.

    Falls back to the original text if no fences are detected. Handles the
    common case where models prepend a short comment before the fence.
    """
    text = text.strip()
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    # Some models start directly with ``` and forget to close it.
    if text.startswith("```"):
        # Strip the opening fence line and any trailing closing fence
        without_open = text.split("\n", 1)[1] if "\n" in text else ""
        return without_open.removesuffix("```").strip()
    return text


# ---------- Language detection -------------------------------------------------------------------

_PT_INDICATORS = [
    r"\bfunção\b", r"\bclasse\b", r"\bvariável\b", r"\bretorno\b",
    r"\bnão\b", r"\bação\b", r"\busuário\b", r"\bsenha\b",
    r"\barquivo\b", r"\bcaminho\b", r"\bnome\b", r"\bvalor\b",
    r"\bconfiguração\b", r"\bpergunta\b", r"\bresposta\b",
    r"\bçã[oe]\b", r"\bõ[ae]s\b",
]
_EN_INDICATORS = [
    r"\bfunction\b", r"\bclass\b", r"\bvariable\b", r"\breturn\b",
    r"\bvalue\b", r"\bname\b", r"\bfile\b", r"\bpath\b",
]


def detect_language(code: str) -> str:
    """Heuristic language detection on identifiers and comments.

    Returns one of: 'pt', 'en', 'mixed', 'unknown'. The detection is
    intentionally crude — it only needs to be good enough to skip the
    translator step when the code is already in English.
    """
    pt_hits = sum(len(re.findall(p, code, re.IGNORECASE)) for p in _PT_INDICATORS)
    en_hits = sum(len(re.findall(p, code, re.IGNORECASE)) for p in _EN_INDICATORS)

    if pt_hits == 0 and en_hits == 0:
        return "unknown"
    if pt_hits > en_hits * 1.5:
        return "pt"
    if en_hits > pt_hits * 1.5:
        return "en"
    return "mixed"


# ---------- AST validation -----------------------------------------------------------------------

def validate_syntax(code: str) -> Tuple[bool, str]:
    """Parse code with AST to confirm it's syntactically valid Python."""
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as exc:
        return False, f"SyntaxError at line {exc.lineno}: {exc.msg}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


# ---------- Truncation for prompts ---------------------------------------------------------------

def truncate_for_prompt(code: str, max_chars: int) -> str:
    """Truncate very long files so they fit in the LLM context window."""
    if len(code) <= max_chars:
        return code
    return code[:max_chars] + f"\n\n# ... [truncated {len(code) - max_chars} chars] ..."
