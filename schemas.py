# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#
# Pydantic schemas. Deliberately small: refactored code itself stays as
# plain text (forcing JSON encoding around code corrupts quotes/backslashes
# and degrades model output). Schemas are used only for analysis metadata.
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#

from __future__ import annotations

from typing import List, Literal
from pydantic import BaseModel, Field


# ---------- Drafter output (kept as data, not JSON-wrapped code) ---------------------------------

class DrafterDraft(BaseModel):
    """A single drafter's contribution to the refactor."""
    agent: Literal["translator", "refactorer", "documenter"]
    code: str = Field(..., description="The drafter's version of the code (plain Python, no fences)")
    syntax_valid: bool = Field(..., description="Whether this draft passes ast.parse()")
    syntax_error: str = Field(default="", description="AST error message if syntax_valid is False")


# ---------- Final answer for batch summary -------------------------------------------------------

class FileResult(BaseModel):
    success: bool
    file: str
    output: str = ""
    report: str = ""
    syntax_valid: bool = False
    error: str = ""
