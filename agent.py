# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#
# PYTHON REFACTORING AGENT
# REPOSITORY: https://github.com/cassianorcarneiro/pyrefactor-agent
# CASSIANO RIBEIRO CARNEIRO
#
# Pipeline:
#   load -> { translator | refactorer | documenter } -> aggregate -> report
# Each drafter produces plain Python (not JSON-wrapped). The aggregator merges
# the three drafts; if its output fails AST validation, it is retried, and as
# a last resort the best-validated draft is used as the final output.
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#

from __future__ import annotations

# ---------------------------------------------------------------------------
# Windows console encoding bootstrap.
# Without this, Windows cmd/PowerShell uses cp1252 by default and any emoji
# or accented character in a print statement raises UnicodeEncodeError before
# the program can show a useful message — leaving the user with a console
# that "exits without printing anything". Runs before any other import.
# ---------------------------------------------------------------------------
import sys as _sys
import io as _io

if _sys.platform == "win32":
    try:
        import ctypes as _ctypes
        _ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        _ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    try:
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        try:
            _sys.stdout = _io.TextIOWrapper(_sys.stdout.buffer, encoding="utf-8", errors="replace")
            _sys.stderr = _io.TextIOWrapper(_sys.stderr.buffer, encoding="utf-8", errors="replace")
        except Exception:
            pass

import sys
import difflib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict, Annotated
from operator import add

import ollama
from langgraph.graph import StateGraph, END
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from config import Config
from io_utils import (
    strip_code_fences,
    detect_language,
    validate_syntax,
    truncate_for_prompt,
)
from llm_client import LLMClient


# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#
# Console factory
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#

def make_console() -> Console:
    """Create a Rich Console with predictable behavior across terminals.

    The auto-detection in Rich fails in several Windows environments
    (PowerShell ISE, redirected stdout, older terminals), so we force
    modern terminal behavior explicitly.
    """
    return Console(
        force_terminal=True,
        legacy_windows=False,
        soft_wrap=True,
    )


# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#
# Graph state
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#

class RefactorState(TypedDict, total=False):
    """State shared across all nodes of the refactoring graph."""

    file_path: str
    original_code: str
    language_detected: str   # "pt", "en", "mixed", "unknown"

    drafts: Annotated[List[Dict[str, Any]], add]  # fan-in reducer

    final_code: str
    change_report: str
    syntax_valid: bool
    error: str


# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#
# Prompt loading
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#

def load_prompts(prompts_dir: str) -> Dict[str, str]:
    """Load all versioned prompt files. Falls back to a sibling directory
    of agent.py if the configured path does not exist (handles running
    from an arbitrary working directory)."""
    base = Path(prompts_dir)
    if not base.exists():
        alt = Path(__file__).parent / prompts_dir
        if alt.exists():
            base = alt
    needed = {
        "translator": "01_translator.txt",
        "refactorer": "02_refactorer.txt",
        "documenter": "03_documenter.txt",
        "aggregator": "04_aggregator.txt",
    }
    out: Dict[str, str] = {}
    for name, fname in needed.items():
        p = base / fname
        if not p.exists():
            raise FileNotFoundError(
                f"Prompt file '{fname}' not found in {base.resolve()}. "
                f"Prompts are expected as text files alongside agent.py."
            )
        out[name] = p.read_text(encoding="utf-8").strip()
    return out


# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#
# Core class
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#

@dataclass
class PythonRefactorAgent:
    """Multi-agent Python code refactoring assistant.

    Three specialist agents run in parallel on each input file:
      * Translator   - converts identifiers/strings/comments to English
      * Refactorer   - improves structure, applies PEP 8, adds type hints
      * Documenter   - adds docstrings and inline comments

    A final aggregator merges the three drafts into a single cohesive output.
    The aggregated code is validated with ast.parse() and retried/fallen-back
    if invalid.
    """

    config: Config

    def __post_init__(self) -> None:
        self.console = make_console()

        self.console.print("[dim]-> Loading prompts...[/dim]")
        self.prompts = load_prompts(self.config.prompts_dir)

        self.console.print("[dim]-> Verifying Ollama models...[/dim]")
        self._check_model()

        self.console.print("[dim]-> Initializing LLM client...[/dim]")
        self.llm = LLMClient(
            base_url=self.config.ollama_base_url,
            default_model=self.config.ollama_model,
        )

        self.console.print("[dim]-> Building agent graph...[/dim]")
        self.app = self._build_graph()

        self.console.print("[green]Ready.[/green]\n")

    # -------------------------------------------------------------------------
    # Model verification
    # -------------------------------------------------------------------------

    def _check_model(self) -> None:
        """Verify Ollama is reachable and resolve the configured models."""
        try:
            client = ollama.Client(host=self.config.ollama_base_url)
            models_response = client.list()
            model_details: List[Dict[str, Any]] = []

            if hasattr(models_response, "models") and models_response.models:
                for model in models_response.models:
                    model_details.append({
                        "name": model.model,
                        "size": getattr(model, "size", 0) or 0,
                        "modified": getattr(model, "modified_at", None),
                        "parameters": (
                            getattr(model.details, "parameter_size", "N/A")
                            if model.details else "N/A"
                        ),
                    })

            if not model_details:
                raise RuntimeError(
                    "No models available in Ollama. "
                    "Install one with: ollama pull deepseek-coder"
                )

            # Resolve the default model
            self.config.ollama_model = self._resolve_model(
                self.config.ollama_model, model_details, "default"
            )

            # Resolve optional per-agent overrides
            for attr, label in [
                ("ollama_model_translator", "translator"),
                ("ollama_model_refactorer", "refactorer"),
                ("ollama_model_documenter", "documenter"),
                ("ollama_model_aggregator", "aggregator"),
            ]:
                requested = getattr(self.config, attr)
                if requested:
                    setattr(self.config, attr, self._resolve_model(
                        requested, model_details, label
                    ))

        except Exception as exc:
            raise RuntimeError(
                f"Could not connect to Ollama at {self.config.ollama_base_url}.\n"
                f"   Original error: {exc}\n\n"
                "Possible fixes:\n"
                "   1. Start Ollama:                ollama serve\n"
                "   2. Pull a code-specialized model: ollama pull deepseek-coder\n"
                "   3. Recommended models for code: deepseek-coder, qwen2.5-coder, codellama\n"
                "   4. Confirm the URL in config.ollama_base_url"
            ) from exc

    def _resolve_model(
        self,
        requested: str,
        model_details: List[Dict[str, Any]],
        label: str,
    ) -> str:
        """Match exact -> prefix -> substring -> first-available. Deterministic."""
        req_low = requested.lower()

        # 1. Exact match
        match = [m for m in model_details if m["name"].lower() == req_low]
        if match:
            self._log_model(label, match[0], exact_match=True)
            return match[0]["name"]
        # 2. Prefix match
        match = [m for m in model_details if m["name"].lower().startswith(req_low)]
        if match:
            self._log_model(label, match[0], exact_match=False)
            return match[0]["name"]
        # 3. Substring match
        match = [m for m in model_details if req_low in m["name"].lower()]
        if match:
            self._log_model(label, match[0], exact_match=False)
            return match[0]["name"]

        # Fallback
        chosen = model_details[0]
        self.console.print(Panel(
            f"[yellow]'{requested}' not found.[/yellow]\n"
            f"Falling back to: [bold]{chosen['name']}[/bold]\n"
            f"[dim]For best results on Python code, install:[/dim] "
            f"[cyan]ollama pull deepseek-coder[/cyan]",
            title=f"Model ({label}) — fallback",
            border_style="yellow",
        ))
        return chosen["name"]

    def _log_model(self, label: str, chosen: Dict[str, Any], exact_match: bool) -> None:
        size_gb = chosen["size"] / 1024 / 1024 / 1024
        suffix = "" if exact_match else " [dim](non-exact match)[/dim]"
        modified_str = ""
        if chosen.get("modified"):
            try:
                modified_str = f"\nModified: {chosen['modified'].strftime('%Y-%m-%d %H:%M')}"
            except Exception:
                pass
        self.console.print(Panel(
            f"[green]Model ({label}):[/green] {chosen['name']}{suffix}\n"
            f"Size: {size_gb:.1f} GB\n"
            f"Parameters: {chosen['parameters']}{modified_str}",
            title=f"Ollama Model ({label})",
            border_style="green",
        ))

    # -------------------------------------------------------------------------
    # Graph nodes
    # -------------------------------------------------------------------------

    def _node_load(self, state: RefactorState) -> Dict[str, Any]:
        """Load the source file from disk and detect its source language."""
        self.console.print("[dim cyan]>> [load] Reading source file...[/dim cyan]")

        path = Path(state["file_path"])
        if not path.exists():
            return {"error": f"File not found: {path}"}
        if path.suffix not in self.config.supported_extensions:
            return {"error": f"Unsupported extension: {path.suffix}"}

        size_kb = path.stat().st_size / 1024
        if size_kb > self.config.max_file_size_kb:
            return {"error": (
                f"File too large: {size_kb:.1f} KB "
                f"(max {self.config.max_file_size_kb} KB)"
            )}

        try:
            code = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Fall back to latin-1 (common for legacy Brazilian code)
            code = path.read_text(encoding="latin-1")

        language = detect_language(code)

        self.console.print(
            f"[dim cyan]>> [load] {path.name} loaded "
            f"({len(code)} chars, language={language})[/dim cyan]"
        )

        return {"original_code": code, "language_detected": language, "error": ""}

    def _run_drafter(
        self,
        state: RefactorState,
        agent_name: str,
        prompt_key: str,
        temperature: float,
        model_attr: str,
        skip_if: bool = False,
    ) -> Dict[str, Any]:
        """Common skeleton for all drafter nodes."""
        self.console.print(f"[dim cyan]>> [{agent_name}] Running...[/dim cyan]")

        if state.get("error"):
            return {"drafts": []}

        original = state["original_code"]

        # Some drafters have an opt-out (e.g. translator skipped if already English)
        if skip_if:
            self.console.print(
                f"[dim yellow]>> [{agent_name}] Skipped (already English).[/dim yellow]"
            )
            valid, _err = validate_syntax(original)
            return {"drafts": [{
                "agent": agent_name,
                "code": original,
                "syntax_valid": valid,
                "syntax_error": "",
            }]}

        code = truncate_for_prompt(original, self.config.max_code_chars)
        model = getattr(self.config, model_attr) or self.config.ollama_model

        prompt = (
            self.prompts[prompt_key]
            + (f"\n\nSource language detected: {state['language_detected']}"
               if agent_name == "translator" else "")
            + f"\n\nORIGINAL CODE:\n```python\n{code}\n```\n\n"
            f"Output the resulting Python code now:"
        )

        try:
            raw = self.llm.chat_text(prompt=prompt, temperature=temperature, model=model)
        except Exception as exc:
            self.console.print(
                f"[yellow]>> [{agent_name}] LLM call failed: {exc}. "
                f"Using original code as draft.[/yellow]"
            )
            valid, err = validate_syntax(original)
            return {"drafts": [{
                "agent": agent_name,
                "code": original,
                "syntax_valid": valid,
                "syntax_error": err,
            }]}

        result = strip_code_fences(raw)
        valid, err = validate_syntax(result)

        if not valid:
            self.console.print(
                f"[yellow]>> [{agent_name}] Output failed AST: {err}[/yellow]"
            )

        self.console.print(f"[dim cyan]>> [{agent_name}] Done.[/dim cyan]")
        return {"drafts": [{
            "agent": agent_name,
            "code": result,
            "syntax_valid": valid,
            "syntax_error": err,
        }]}

    def _node_translator(self, state: RefactorState) -> Dict[str, Any]:
        # Skip when feature is disabled OR code is already in English
        skip = (
            not self.config.translate_to_english
            or state.get("language_detected") == "en"
        )
        return self._run_drafter(
            state, "translator", "translator",
            self.config.temperature_translator,
            "ollama_model_translator",
            skip_if=skip,
        )

    def _node_refactorer(self, state: RefactorState) -> Dict[str, Any]:
        return self._run_drafter(
            state, "refactorer", "refactorer",
            self.config.temperature_refactorer,
            "ollama_model_refactorer",
        )

    def _node_documenter(self, state: RefactorState) -> Dict[str, Any]:
        return self._run_drafter(
            state, "documenter", "documenter",
            self.config.temperature_documenter,
            "ollama_model_documenter",
        )

    def _node_aggregate(self, state: RefactorState) -> Dict[str, Any]:
        """Merge the three specialist drafts into the final version.

        Strategy:
          1. Run the aggregator LLM call.
          2. If output fails AST, retry up to N times with explicit feedback.
          3. If still invalid, fall back to the best-validated draft
             (refactorer > translator > documenter > original).
        """
        self.console.print("[dim cyan]>> [aggregator] Merging drafts...[/dim cyan]")

        if state.get("error"):
            return {"final_code": "", "syntax_valid": False}

        drafts = state.get("drafts", [])
        if not drafts:
            valid, err = validate_syntax(state["original_code"])
            return {
                "final_code": state["original_code"],
                "syntax_valid": valid,
                "error": err if not valid else "",
            }

        by_agent = {d["agent"]: d for d in drafts}
        original = truncate_for_prompt(state["original_code"], self.config.max_code_chars)
        model = self.config.ollama_model_aggregator or self.config.ollama_model

        def _format_draft(name: str) -> str:
            d = by_agent.get(name)
            if not d:
                return "# (no draft)"
            note = "" if d["syntax_valid"] else f"\n# (NOTE: this draft FAILED AST validation: {d['syntax_error']})"
            return note + "\n" + truncate_for_prompt(d["code"], self.config.max_code_chars)

        base_prompt = (
            self.prompts["aggregator"]
            + f"\n\n=== ORIGINAL CODE (reference for behavior preservation) ===\n"
              f"```python\n{original}\n```\n\n"
            f"=== TRANSLATOR DRAFT ===\n```python{_format_draft('translator')}\n```\n\n"
            f"=== REFACTORER DRAFT ===\n```python{_format_draft('refactorer')}\n```\n\n"
            f"=== DOCUMENTER DRAFT ===\n```python{_format_draft('documenter')}\n```\n\n"
            "Output the FINAL merged Python code now:"
        )

        last_err = ""
        final_code = ""
        valid = False

        for attempt in range(self.config.aggregator_ast_retries + 1):
            prompt = base_prompt
            if attempt > 0 and last_err:
                prompt += (
                    f"\n\nNOTE: Previous attempt produced syntactically invalid Python:\n"
                    f"{last_err}\nPlease fix it and output valid Python this time."
                )
            try:
                raw = self.llm.chat_text(
                    prompt=prompt,
                    temperature=self.config.temperature_aggregator,
                    model=model,
                )
            except Exception as exc:
                self.console.print(f"[red]>> [aggregator] LLM call failed: {exc}[/red]")
                break

            candidate = strip_code_fences(raw)
            valid, err = validate_syntax(candidate)
            final_code = candidate
            last_err = err
            if valid:
                break
            self.console.print(
                f"[yellow]>> [aggregator] Attempt {attempt+1} failed AST: {err}[/yellow]"
            )

        # Fallback: pick the best valid draft if aggregation failed
        if not valid and self.config.validate_syntax:
            self.console.print(
                "[yellow]>> [aggregator] Falling back to best-validated draft.[/yellow]"
            )
            for preference in ("refactorer", "translator", "documenter"):
                d = by_agent.get(preference)
                if d and d["syntax_valid"]:
                    final_code = d["code"]
                    valid = True
                    last_err = ""
                    self.console.print(
                        f"[yellow]>> [aggregator] Using {preference} draft as fallback.[/yellow]"
                    )
                    break
            else:
                # Last resort: original code (always valid by definition,
                # since it was successfully read from disk)
                final_code = state["original_code"]
                valid, last_err = validate_syntax(final_code)
                self.console.print(
                    "[red]>> [aggregator] All drafts failed; keeping original code.[/red]"
                )

        self.console.print("[dim cyan]>> [aggregator] Done.[/dim cyan]")
        return {
            "final_code": final_code,
            "syntax_valid": valid,
            "error": last_err if not valid else "",
        }

    def _node_report(self, state: RefactorState) -> Dict[str, Any]:
        """Build a markdown report describing what changed."""
        self.console.print("[dim cyan]>> [report] Generating change report...[/dim cyan]")

        if state.get("error") and not state.get("final_code"):
            return {"change_report": f"# Error\n\n{state['error']}"}

        original = state["original_code"]
        final = state["final_code"]

        original_lines = original.splitlines()
        final_lines = final.splitlines()

        diff_lines = list(difflib.unified_diff(
            original_lines, final_lines,
            fromfile="original", tofile="refactored",
            lineterm="", n=2,
        ))
        diff_preview = "\n".join(diff_lines[:200])
        if len(diff_lines) > 200:
            diff_preview += f"\n... [{len(diff_lines) - 200} more diff lines truncated]"

        added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))

        # Per-draft AST status table
        draft_rows = []
        for d in state.get("drafts", []):
            status = "valid" if d["syntax_valid"] else f"invalid: {d['syntax_error']}"
            draft_rows.append(f"| {d['agent']} | {status} |")
        draft_table = "\n".join(draft_rows) if draft_rows else "| (no drafts) | — |"

        report = f"""# Refactoring Report

**File:** `{state['file_path']}`
**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**Model:** {self.config.ollama_model}

## Summary

| Metric | Original | Refactored |
|--------|----------|------------|
| Lines | {len(original_lines)} | {len(final_lines)} |
| Characters | {len(original)} | {len(final)} |
| Source language detected | `{state['language_detected']}` | `en` |
| Syntax valid (AST) | — | {'Yes' if state['syntax_valid'] else 'No'} |

**Changes:** +{added} / -{removed} lines

## Per-Drafter AST Status

| Drafter | Status |
|---------|--------|
{draft_table}

## Applied Transformations

- Translation to English (variables, comments, strings)
- PEP 8 formatting and import ordering
- Type hints added to public APIs
- Docstrings added (Google/NumPy style)
- Modern Python idioms (f-strings, pathlib, etc.)
- Section dividers and inline comments where useful
- AST syntax validation with retry-and-fallback

## Diff Preview

```diff
{diff_preview if diff_preview else '(no changes)'}
```

## Notes

- Behavior preservation was the top priority — semantic equivalence cannot
  be guaranteed automatically. Review and run tests before deploying.
- If aggregation produced invalid syntax, the agent retried and then fell
  back to the best-validated drafter output.
"""
        return {"change_report": report}

    # -------------------------------------------------------------------------
    # Graph wiring
    # -------------------------------------------------------------------------

    def _build_graph(self):
        """Wire all nodes into a LangGraph state machine."""
        g = StateGraph(RefactorState)

        g.add_node("load", self._node_load)
        g.add_node("translator", self._node_translator)
        g.add_node("refactorer", self._node_refactorer)
        g.add_node("documenter", self._node_documenter)
        g.add_node("aggregate", self._node_aggregate)
        g.add_node("report", self._node_report)

        g.set_entry_point("load")

        # Fan-out: load -> 3 specialists in parallel
        g.add_edge("load", "translator")
        g.add_edge("load", "refactorer")
        g.add_edge("load", "documenter")

        # Fan-in: 3 specialists -> aggregator
        g.add_edge("translator", "aggregate")
        g.add_edge("refactorer", "aggregate")
        g.add_edge("documenter", "aggregate")

        g.add_edge("aggregate", "report")
        g.add_edge("report", END)

        return g.compile()

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def refactor_file(self, file_path: str) -> Dict[str, Any]:
        """Refactor a single Python file. Returns paths and status info."""
        path = Path(file_path).resolve()

        # Fresh state every call — we never share state between files
        init_state: RefactorState = {
            "file_path": str(path),
            "original_code": "",
            "language_detected": "",
            "drafts": [],
            "final_code": "",
            "change_report": "",
            "syntax_valid": False,
            "error": "",
        }

        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=self.console,
                transient=True,
            ) as progress:
                progress.add_task(description=f"Refactoring {path.name}...", total=None)
                result = self.app.invoke(init_state)
        except Exception as exc:
            self.console.print(Panel(
                f"Unexpected pipeline error: {exc}",
                title=f"Failed: {path.name}",
                border_style="red",
            ))
            self.console.print_exception()
            return {"success": False, "error": str(exc), "file": str(path)}

        if result.get("error") and not result.get("final_code"):
            self.console.print(Panel(
                result["error"],
                title=f"Failed: {path.name}",
                border_style="red",
            ))
            return {"success": False, "error": result["error"], "file": str(path)}

        # Write outputs side by side with the original
        out_code_path = path.with_name(
            f"{path.stem}{self.config.output_suffix}{path.suffix}"
        )
        out_report_path = path.with_name(
            f"{path.stem}{self.config.output_suffix}{self.config.report_suffix}.md"
        )

        try:
            out_code_path.write_text(result["final_code"], encoding="utf-8")
            out_report_path.write_text(result["change_report"], encoding="utf-8")
        except OSError as exc:
            self.console.print(Panel(
                f"Could not write output files: {exc}",
                title=f"Failed: {path.name}",
                border_style="red",
            ))
            return {"success": False, "error": str(exc), "file": str(path)}

        status_color = "green" if result["syntax_valid"] else "yellow"
        status_text = "Yes" if result["syntax_valid"] else "No - manual review needed"

        self.console.print(Panel(
            f"Refactored:  [bold]{out_code_path}[/bold]\n"
            f"Report:      [bold]{out_report_path}[/bold]\n"
            f"Syntax OK:   {status_text}",
            title=f"Done: {path.name}",
            border_style=status_color,
        ))

        return {
            "success": True,
            "file": str(path),
            "output": str(out_code_path),
            "report": str(out_report_path),
            "syntax_valid": result["syntax_valid"],
        }

    def refactor_path(self, target: str, recursive: bool = True) -> List[Dict[str, Any]]:
        """Refactor a single file OR every Python file in a directory."""
        target_path = Path(target).resolve()

        if not target_path.exists():
            self.console.print(f"[red]Path not found:[/red] {target_path}")
            return []

        if target_path.is_file():
            return [self.refactor_file(str(target_path))]

        # Directory mode: collect all matching files (skip already-refactored ones)
        pattern = "**/*" if recursive else "*"
        files = [
            p for p in target_path.glob(pattern)
            if p.is_file()
            and p.suffix in self.config.supported_extensions
            and self.config.output_suffix not in p.stem
        ]

        if not files:
            self.console.print(f"[yellow]No Python files found in:[/yellow] {target_path}")
            return []

        self.console.print(Panel(
            f"Found [bold]{len(files)}[/bold] Python file(s) to refactor in:\n{target_path}",
            title="Batch Mode",
            border_style="cyan",
        ))

        results: List[Dict[str, Any]] = []
        for idx, file in enumerate(files, 1):
            self.console.print(f"\n[bold cyan]-- [{idx}/{len(files)}] --[/bold cyan]")
            results.append(self.refactor_file(str(file)))

        # Batch summary
        ok = sum(1 for r in results if r.get("success"))
        valid = sum(1 for r in results if r.get("syntax_valid"))
        self.console.print(Panel(
            f"Total processed: {len(results)}\n"
            f"Success:       {ok}\n"
            f"Syntax valid:  {valid}\n"
            f"Failed:        {len(results) - ok}",
            title="Batch Summary",
            border_style="green" if ok == len(results) else "yellow",
        ))

        return results


# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#
# CLI
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#

def _print_banner(console: Console) -> None:
    console.print(Panel(
        "[bold]Python Refactoring Agent[/bold]\n"
        "Multi-agent system: Translator + Refactorer + Documenter\n\n"
        "[dim]Translates code to English, applies PEP 8, adds type hints,\n"
        "writes docstrings, and validates the result with AST parsing.[/dim]",
        title="Refactor Agent",
        border_style="blue",
    ))


def _print_help(console: Console) -> None:
    console.print(Panel(
        "Enter a [bold]file path[/bold] (e.g.  /path/to/script.py)\n"
        "Or a [bold]directory path[/bold] (all .py files inside will be processed)\n\n"
        "Commands:\n"
        "  [cyan]/help[/cyan]              show this help\n"
        "  [cyan]/recursive on|off[/cyan]  toggle recursive directory traversal\n"
        "  [cyan]exit[/cyan] | [cyan]quit[/cyan]      leave the agent",
        title="Usage",
        border_style="white",
    ))


def main() -> None:
    console = make_console()

    # Explicit start log — if you don't see this line, it's a terminal/encoding problem
    try:
        console.print("[bold]Starting Python Refactoring Agent...[/bold]")
    except Exception as exc:
        try:
            with open("pyrefactor_startup_error.log", "w", encoding="utf-8") as f:
                f.write(f"Failed to write to console: {exc}\n")
                f.write("Try running `chcp 65001` (Windows cmd) before `python agent.py`.\n")
        except Exception:
            pass
        try:
            print("Starting Python Refactoring Agent... (terminal without Rich support)")
        except Exception:
            return

    config = Config()

    try:
        agent = PythonRefactorAgent(config=config)
    except FileNotFoundError as exc:
        console.print(f"\n[bold red]Missing prompt file:[/bold red]\n   {exc}")
        console.print(
            "\n[yellow]Prompts live in ./prompts/ next to agent.py.[/yellow]\n"
            "Make sure that directory was copied alongside the .py files."
        )
        return
    except RuntimeError as exc:
        console.print(f"\n[bold red]{exc}[/bold red]")
        return
    except Exception:
        console.print("\n[bold red]Unexpected initialization failure:[/bold red]")
        console.print_exception()
        return

    _print_banner(agent.console)
    _print_help(agent.console)

    # One-shot CLI usage:  python agent.py /path/to/file_or_dir
    if len(sys.argv) > 1:
        agent.refactor_path(sys.argv[1], recursive=True)
        return

    # Interactive REPL
    recursive = True
    while True:
        agent.console.print()
        try:
            user_input = input("Path or command > ").strip()
        except (EOFError, KeyboardInterrupt):
            agent.console.print("\nGoodbye.")
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            agent.console.print("Goodbye.")
            break
        if user_input.lower() == "/help":
            _print_help(agent.console)
            continue
        if user_input.lower() == "/recursive on":
            recursive = True
            agent.console.print("[green]Recursive directory mode enabled.[/green]")
            continue
        if user_input.lower() == "/recursive off":
            recursive = False
            agent.console.print("[red]Recursive directory mode disabled.[/red]")
            continue

        agent.refactor_path(user_input, recursive=recursive)


if __name__ == "__main__":
    # Note: deliberately NOT calling os.system("clear") here. In some
    # terminals (Windows without TERM, IDEs, redirected output) it can
    # hide initial error messages. Run `clear && python agent.py` if you
    # want a fresh screen.
    main()
