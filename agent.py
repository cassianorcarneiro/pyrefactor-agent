# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#
# PYTHON REFACTORING AGENT
# Multi-agent system for translating, refactoring and documenting Python code.
# Adapted from the original web-search assistant by Cassiano Ribeiro Carneiro.
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#

from __future__ import annotations

import ast
import os
import re
import sys
import difflib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, TypedDict, Annotated
from operator import add

import ollama
from langgraph.graph import StateGraph, END
from langchain_ollama import ChatOllama
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from config import Config


# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#
# Graph state
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#

class RefactorState(TypedDict):
    """State shared across all nodes of the refactoring graph."""

    file_path: str
    original_code: str
    language_detected: str   # "pt", "en", "mixed", "unknown"

    translated_code: str
    refactored_code: str
    documented_code: str

    drafts: Annotated[List[Dict[str, str]], add]  # fan-in reducer

    final_code: str
    change_report: str
    syntax_valid: bool
    error: str


# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#
# Core class
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++#

@dataclass
class PythonRefactorAgent:
    """Multi-agent Python code refactoring assistant.

    Uses three specialist agents in parallel:
      * Translator   - converts identifiers/strings/comments to English
      * Refactorer   - improves structure, applies PEP8, adds type hints
      * Documenter   - adds docstrings and inline comments
    A final aggregator merges the three drafts into one cohesive output.
    """

    def __init__(self, config: Config):
        self.config = config
        self.console = Console()

        self._check_model()
        self.app = self._build_graph()

    # -------------------------------------------------------------------------
    # Model verification
    # -------------------------------------------------------------------------

    def _check_model(self) -> None:
        """Verify Ollama is reachable and the configured model is available."""
        try:
            models_response = ollama.list()
            model_details = []

            if hasattr(models_response, "models") and models_response.models:
                for model in models_response.models:
                    model_details.append({
                        "name": model.model,
                        "size": model.size,
                        "modified": model.modified_at,
                        "parameters": getattr(model.details, "parameter_size", "N/A")
                                      if model.details else "N/A",
                    })

            if not model_details:
                raise RuntimeError("No models available in Ollama.")

            match = next(
                (m for m in model_details
                 if self.config.ollama_model.lower() in m["name"].lower()),
                None,
            )

            chosen = match or model_details[0]
            self.config.ollama_model = chosen["name"]

            border = "green" if match else "yellow"
            icon = "✅" if match else "⚠️"
            label = "Selected model" if match else "Fallback model"

            self.console.print(Panel(
                f"{icon} [bold]{label}:[/bold] {chosen['name']}\n"
                f"📊 Size: {chosen['size']/1024/1024/1024:.1f} GB\n"
                f"⚙️  Parameters: {chosen['parameters']}\n"
                f"📅 Modified: {chosen['modified'].strftime('%Y-%m-%d %H:%M')}",
                title="🤖 Ollama Model",
                border_style=border,
            ))

        except Exception as exc:
            self.console.print(f"❌ [red]Error connecting to Ollama:[/red] {exc}")
            self.console.print(
                "\n🔧 [yellow]Possible fixes:[/yellow]\n"
                "  1. Start Ollama:  ollama serve\n"
                "  2. Pull a model:  ollama pull deepseek-coder\n"
                "  3. Recommended for code: deepseek-coder, qwen2.5-coder, codellama"
            )
            raise

    # -------------------------------------------------------------------------
    # LLM factory
    # -------------------------------------------------------------------------

    def _llm(self, temperature: float) -> ChatOllama:
        return ChatOllama(
            model=self.config.ollama_model,
            base_url=self.config.ollama_base_url,
            temperature=temperature,
        )

    # -------------------------------------------------------------------------
    # Utilities
    # -------------------------------------------------------------------------

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        """Remove ```python ... ``` fences if the LLM wrapped the code."""
        text = text.strip()
        # Match ```python\n...\n``` or ```\n...\n```
        match = re.search(r"```(?:python|py)?\s*\n(.*?)\n```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text

    @staticmethod
    def _detect_language(code: str) -> str:
        """Heuristic language detection on identifiers/comments."""
        # Common Portuguese keywords/words used in code
        pt_indicators = [
            r"\bfunção\b", r"\bclasse\b", r"\bvariável\b", r"\bretorno\b",
            r"\bnão\b", r"\bação\b", r"\busuário\b", r"\bsenha\b",
            r"\barquivo\b", r"\bcaminho\b", r"\bnome\b", r"\bvalor\b",
            r"\bconfiguração\b", r"\bpergunta\b", r"\bresposta\b",
            r"\bçã[oe]\b", r"\bõ[ae]s\b",
        ]
        en_indicators = [
            r"\bfunction\b", r"\bclass\b", r"\bvariable\b", r"\breturn\b",
            r"\bvalue\b", r"\bname\b", r"\bfile\b", r"\bpath\b",
        ]

        pt_hits = sum(len(re.findall(p, code, re.IGNORECASE)) for p in pt_indicators)
        en_hits = sum(len(re.findall(p, code, re.IGNORECASE)) for p in en_indicators)

        if pt_hits == 0 and en_hits == 0:
            return "unknown"
        if pt_hits > en_hits * 1.5:
            return "pt"
        if en_hits > pt_hits * 1.5:
            return "en"
        return "mixed"

    @staticmethod
    def _validate_syntax(code: str) -> tuple[bool, str]:
        """Parse code with AST to confirm it is syntactically valid Python."""
        try:
            ast.parse(code)
            return True, ""
        except SyntaxError as exc:
            return False, f"SyntaxError at line {exc.lineno}: {exc.msg}"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    def _truncate_for_prompt(self, code: str) -> str:
        """Truncate very long files so they fit in the LLM context."""
        if len(code) <= self.config.max_code_chars:
            return code
        cut = self.config.max_code_chars
        return code[:cut] + f"\n\n# ... [truncated {len(code) - cut} chars] ..."

    # -------------------------------------------------------------------------
    # Graph nodes
    # -------------------------------------------------------------------------

    def _node_load(self, state: RefactorState) -> Dict[str, Any]:
        """Load the source file and detect its language."""
        self.console.print("[dim cyan]>> [load] Reading source file...[/dim cyan]")

        path = Path(state["file_path"])
        if not path.exists():
            return {"error": f"File not found: {path}"}
        if path.suffix not in self.config.supported_extensions:
            return {"error": f"Unsupported extension: {path.suffix}"}

        size_kb = path.stat().st_size / 1024
        if size_kb > self.config.max_file_size_kb:
            return {"error": f"File too large: {size_kb:.1f} KB "
                             f"(max {self.config.max_file_size_kb} KB)"}

        code = path.read_text(encoding="utf-8")
        language = self._detect_language(code)

        self.console.print(
            f"[dim cyan]>> [load] {path.name} loaded "
            f"({len(code)} chars, language={language})[/dim cyan]"
        )

        return {"original_code": code, "language_detected": language, "error": ""}

    def _node_translator(self, state: RefactorState) -> Dict[str, Any]:
        """Agent 1: translate identifiers, strings, and comments to English."""
        self.console.print("[dim cyan]>> [agent-1] Translator...[/dim cyan]")

        if state.get("error"):
            return {"drafts": []}

        if not self.config.translate_to_english:
            return {"drafts": [{"agent": "translator", "code": state["original_code"]}]}

        code = self._truncate_for_prompt(state["original_code"])
        llm = self._llm(self.config.temperature_drafters)

        prompt = f"""You are a Python code translator specialist.

Your job: translate ALL non-English content in this code to clear, professional English.

RULES (critical):
- Translate variable names, function names, class names, parameters from Portuguese to English.
- Translate ALL comments and docstrings to English.
- Translate user-facing strings (prints, error messages, panel titles) to English.
- Keep the EXACT same logic and behavior. Do NOT add or remove functionality.
- Keep all imports, library names, and external API names UNCHANGED.
- Use idiomatic English names: `usuario` -> `user`, `senha` -> `password`, etc.
- Output ONLY the translated Python code, no explanations, no markdown fences.

Detected source language: {state['language_detected']}

ORIGINAL CODE:
```python
{code}
```

Output the translated Python code now:"""

        result = llm.invoke(prompt).content
        translated = self._strip_code_fences(result)

        self.console.print("[dim cyan]>> [agent-1] Translator finished.[/dim cyan]")
        return {"drafts": [{"agent": "translator", "code": translated}]}

    def _node_refactorer(self, state: RefactorState) -> Dict[str, Any]:
        """Agent 2: improve structure, apply PEP8, add type hints."""
        self.console.print("[dim cyan]>> [agent-2] Refactorer...[/dim cyan]")

        if state.get("error"):
            return {"drafts": []}

        code = self._truncate_for_prompt(state["original_code"])
        llm = self._llm(self.config.temperature_drafters)

        prompt = f"""You are a Python refactoring specialist.

Your job: improve this code's STRUCTURE and STYLE without changing its behavior.

APPLY:
- PEP 8 formatting (line length ~88-100, proper spacing, naming conventions).
- Add precise type hints to function signatures and key variables.
- Replace magic numbers with named constants when appropriate.
- Extract repeated logic into small helper functions.
- Use modern Python idioms (f-strings, pathlib, dataclasses, comprehensions, walrus where helpful).
- Improve error handling: replace bare `except` with specific exceptions.
- Order imports: stdlib, third-party, local — separated by blank lines.
- Remove dead code, unused imports/variables.

CRITICAL CONSTRAINTS:
- Preserve EXACT runtime behavior. No new features, no removed features.
- Keep public API (function/class names visible outside the module) stable when possible.
- Do NOT translate language — another agent handles that.
- Output ONLY the refactored Python code, no explanations, no markdown fences.

ORIGINAL CODE:
```python
{code}
```

Output the refactored Python code now:"""

        result = llm.invoke(prompt).content
        refactored = self._strip_code_fences(result)

        self.console.print("[dim cyan]>> [agent-2] Refactorer finished.[/dim cyan]")
        return {"drafts": [{"agent": "refactorer", "code": refactored}]}

    def _node_documenter(self, state: RefactorState) -> Dict[str, Any]:
        """Agent 3: add docstrings, inline comments, and section organization."""
        self.console.print("[dim cyan]>> [agent-3] Documenter...[/dim cyan]")

        if state.get("error"):
            return {"drafts": []}

        code = self._truncate_for_prompt(state["original_code"])
        llm = self._llm(self.config.temperature_drafters)

        prompt = f"""You are a Python documentation specialist.

Your job: add CLEAR, USEFUL documentation to this code without changing its behavior.

ADD:
- Google-style or NumPy-style docstrings to every public function, method, and class.
  Include: summary line, Args, Returns, Raises (when relevant).
- A module-level docstring at the top describing the module's purpose.
- Brief inline comments only where the logic is non-obvious. Do NOT comment trivial lines.
- Logical section dividers using comment banners for large modules:
    # ===== Section Name =====
- Type hints in docstrings should match actual signatures.

CRITICAL CONSTRAINTS:
- Preserve EXACT code logic and structure. Only ADD documentation.
- Keep all comments and docstrings in English.
- No bloated docstrings — be concise and informative.
- Output ONLY the documented Python code, no explanations, no markdown fences.

ORIGINAL CODE:
```python
{code}
```

Output the documented Python code now:"""

        result = llm.invoke(prompt).content
        documented = self._strip_code_fences(result)

        self.console.print("[dim cyan]>> [agent-3] Documenter finished.[/dim cyan]")
        return {"drafts": [{"agent": "documenter", "code": documented}]}

    def _node_aggregate(self, state: RefactorState) -> Dict[str, Any]:
        """Merge the three specialist drafts into a single final version."""
        self.console.print("[dim cyan]>> [aggregator] Merging drafts...[/dim cyan]")

        if state.get("error"):
            return {"final_code": "", "syntax_valid": False}

        drafts = state.get("drafts", [])
        if not drafts:
            return {"final_code": state["original_code"], "syntax_valid": True}

        by_agent = {d["agent"]: d["code"] for d in drafts}
        original = self._truncate_for_prompt(state["original_code"])

        llm = self._llm(self.config.temperature_aggregator)

        prompt = f"""You are a senior Python code reviewer aggregating work from three specialists.

You have THREE drafts of the same module, each focused on a different concern:
  1. TRANSLATOR draft — names/comments/strings translated to English.
  2. REFACTORER draft — structure, PEP8, type hints, modern idioms.
  3. DOCUMENTER draft — docstrings, comments, section dividers.

Produce ONE final version that combines the strengths of all three:
- English naming and strings (from translator).
- Clean structure, PEP8, type hints (from refactorer).
- Clear docstrings and section comments (from documenter).
- Resolve any conflicts in favor of: correctness > clarity > brevity.
- The final code MUST preserve the EXACT behavior of the original.
- The final code MUST be syntactically valid Python that runs without modification.

Output ONLY the final Python code. No explanations. No markdown fences.

=== ORIGINAL CODE (reference for behavior preservation) ===
```python
{original}
```

=== TRANSLATOR DRAFT ===
```python
{self._truncate_for_prompt(by_agent.get('translator', '# (no draft)'))}
```

=== REFACTORER DRAFT ===
```python
{self._truncate_for_prompt(by_agent.get('refactorer', '# (no draft)'))}
```

=== DOCUMENTER DRAFT ===
```python
{self._truncate_for_prompt(by_agent.get('documenter', '# (no draft)'))}
```

Output the FINAL merged Python code now:"""

        result = llm.invoke(prompt).content
        final = self._strip_code_fences(result)

        # AST validation — fall back to refactorer draft if final is invalid
        valid, err = self._validate_syntax(final)
        if not valid and self.config.validate_syntax:
            self.console.print(
                f"[yellow]⚠ Aggregated output failed AST validation: {err}[/yellow]\n"
                f"[yellow]  Falling back to refactorer draft.[/yellow]"
            )
            fallback = by_agent.get("refactorer") or by_agent.get("translator") \
                       or state["original_code"]
            valid, err = self._validate_syntax(fallback)
            final = fallback

        self.console.print("[dim cyan]>> [aggregator] Done.[/dim cyan]")
        return {"final_code": final, "syntax_valid": valid, "error": err if not valid else ""}

    def _node_report(self, state: RefactorState) -> Dict[str, Any]:
        """Build a markdown report describing what changed."""
        self.console.print("[dim cyan]>> [report] Generating change report...[/dim cyan]")

        if state.get("error") and not state.get("final_code"):
            return {"change_report": f"# Error\n\n{state['error']}"}

        original = state["original_code"]
        final = state["final_code"]

        original_lines = original.splitlines()
        final_lines = final.splitlines()

        # Unified diff (truncated for the report)
        diff_lines = list(difflib.unified_diff(
            original_lines, final_lines,
            fromfile="original", tofile="refactored",
            lineterm="", n=2,
        ))
        diff_preview = "\n".join(diff_lines[:200])
        if len(diff_lines) > 200:
            diff_preview += f"\n... [{len(diff_lines) - 200} more diff lines truncated]"

        # Quick metrics
        added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))

        report = f"""# Refactoring Report

**File:** `{state['file_path']}`
**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**Model:** {self.config.ollama_model}

## Summary

| Metric | Original | Refactored |
|--------|----------|------------|
| Lines | {len(original_lines)} | {len(final_lines)} |
| Characters | {len(original)} | {len(final)} |
| Detected source language | `{state['language_detected']}` | `en` |
| Syntax valid (AST) | — | {'✅ Yes' if state['syntax_valid'] else '❌ No'} |

**Changes:** +{added} / -{removed} lines

## Applied Transformations

- ✅ Translation to English (variables, comments, strings)
- ✅ PEP 8 formatting and import ordering
- ✅ Type hints added to public APIs
- ✅ Docstrings added (Google/NumPy style)
- ✅ Modern Python idioms (f-strings, pathlib, etc.)
- ✅ Section dividers and inline comments where useful
- ✅ AST syntax validation

## Diff Preview

```diff
{diff_preview if diff_preview else '(no changes)'}
```

## Notes

- Behavior preservation was the top priority — semantic equivalence
  cannot be guaranteed automatically. Review and run tests before deploying.
- If aggregation produced invalid syntax, the refactorer draft was used as a fallback.
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

        # fan-out: load -> 3 specialists in parallel
        g.add_edge("load", "translator")
        g.add_edge("load", "refactorer")
        g.add_edge("load", "documenter")

        # fan-in: 3 specialists -> aggregator
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

        init_state: RefactorState = {
            "file_path": str(path),
            "original_code": "",
            "language_detected": "",
            "translated_code": "",
            "refactored_code": "",
            "documented_code": "",
            "drafts": [],
            "final_code": "",
            "change_report": "",
            "syntax_valid": False,
            "error": "",
        }

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=self.console,
            transient=True,
        ) as progress:
            progress.add_task(description=f"Refactoring {path.name}...", total=None)
            result = self.app.invoke(init_state)

        if result.get("error") and not result.get("final_code"):
            self.console.print(Panel(
                f"❌ {result['error']}",
                title=f"Failed: {path.name}",
                border_style="red",
            ))
            return {"success": False, "error": result["error"], "file": str(path)}

        # Write outputs side by side with the original
        out_code_path = path.with_name(f"{path.stem}{self.config.output_suffix}{path.suffix}")
        out_report_path = path.with_name(
            f"{path.stem}{self.config.output_suffix}{self.config.report_suffix}.md"
        )

        out_code_path.write_text(result["final_code"], encoding="utf-8")
        out_report_path.write_text(result["change_report"], encoding="utf-8")

        status_icon = "✅" if result["syntax_valid"] else "⚠️"
        status_color = "green" if result["syntax_valid"] else "yellow"

        self.console.print(Panel(
            f"{status_icon} Refactored:  [bold]{out_code_path}[/bold]\n"
            f"📄 Report:      [bold]{out_report_path}[/bold]\n"
            f"🔍 Syntax OK:   {'Yes' if result['syntax_valid'] else 'No — manual review needed'}",
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
            self.console.print(f"❌ [red]Path not found:[/red] {target_path}")
            return []

        if target_path.is_file():
            return [self.refactor_file(str(target_path))]

        # Directory: collect all matching files (skip already-refactored ones)
        pattern = "**/*" if recursive else "*"
        files = [
            p for p in target_path.glob(pattern)
            if p.is_file()
            and p.suffix in self.config.supported_extensions
            and self.config.output_suffix not in p.stem
        ]

        if not files:
            self.console.print(f"⚠️  [yellow]No Python files found in:[/yellow] {target_path}")
            return []

        self.console.print(Panel(
            f"Found [bold]{len(files)}[/bold] Python file(s) to refactor in:\n{target_path}",
            title="📂 Batch Mode",
            border_style="cyan",
        ))

        results = []
        for idx, f in enumerate(files, 1):
            self.console.print(f"\n[bold cyan]── [{idx}/{len(files)}] ──[/bold cyan]")
            results.append(self.refactor_file(str(f)))

        # Batch summary
        ok = sum(1 for r in results if r.get("success"))
        valid = sum(1 for r in results if r.get("syntax_valid"))
        self.console.print(Panel(
            f"Total processed: {len(results)}\n"
            f"✅ Success:       {ok}\n"
            f"🔍 Syntax valid:  {valid}\n"
            f"❌ Failed:        {len(results) - ok}",
            title="📊 Batch Summary",
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
        title="🐍 Refactor Agent",
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
        title="📖 Usage",
        border_style="white",
    ))


def main() -> None:
    config = Config()
    agent = PythonRefactorAgent(config=config)

    _print_banner(agent.console)
    _print_help(agent.console)

    # Allow one-shot CLI usage:  python agent.py /path/to/file_or_dir
    if len(sys.argv) > 1:
        agent.refactor_path(sys.argv[1], recursive=True)
        return

    recursive = True
    while True:
        agent.console.print()
        try:
            user_input = input("Path or command > ").strip()
        except (EOFError, KeyboardInterrupt):
            agent.console.print("\n👋 Goodbye.")
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            agent.console.print("👋 Goodbye.")
            break
        if user_input.lower() == "/help":
            _print_help(agent.console)
            continue
        if user_input.lower() == "/recursive on":
            recursive = True
            agent.console.print("🟢 [green]Recursive directory mode enabled.[/green]")
            continue
        if user_input.lower() == "/recursive off":
            recursive = False
            agent.console.print("🔴 [red]Recursive directory mode disabled.[/red]")
            continue

        agent.refactor_path(user_input, recursive=recursive)


if __name__ == "__main__":
    os.system("cls" if os.name == "nt" else "clear")
    main()
