# 🐍 PyRefactor Agent

> Multi-agent Python refactoring tool powered by local LLMs.

A privacy-first, offline-capable Python refactoring system that translates, refactors, and documents Python code via three specialized agents — with **AST-validated output** and zero data sharing with proprietary model providers.

<p align="center">
  <img alt="Stack" src="https://img.shields.io/badge/Stack-LangGraph%20%2B%20Ollama-blue?style=for-the-badge">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge">
  <img alt="Status" src="https://img.shields.io/badge/Privacy-Local%20First-success?style=for-the-badge">
</p>

---

## 📦 How it works

The agent runs **three specialists in parallel** on every input file, then merges their work — and validates the merged output with Python's AST module:

```
                ┌──> Translator   (names/comments/strings → English) ──┐
load ──────────┼──> Refactorer   (PEP 8, type hints, idioms)          ┼──> aggregate ──> report ──> END
                └──> Documenter   (docstrings, section dividers)       ┘
```

| Agent | Responsibility |
|-------|----------------|
| **Translator** | Converts identifiers, comments, docstrings, and user-facing strings to English. Skipped automatically when source language is already English |
| **Refactorer** | Applies PEP 8, adds type hints, modernizes idioms, removes dead code |
| **Documenter** | Adds Google-style docstrings, section dividers, and useful inline comments |
| **Aggregator** | Merges the three drafts into a final version (correctness > clarity > brevity), with AST validation, automatic retry on syntax errors, and best-draft fallback |

### Resilience layers

- **Per-drafter AST check** — every draft is parsed with `ast.parse()` and the result is recorded in the report
- **Aggregator retry** — if the aggregator produces invalid Python, it is re-prompted with the error message attached
- **Fallback chain** — if retries fail, the agent picks the best AST-valid draft (refactorer → translator → documenter), and as a last resort keeps the original code untouched
- **Encoding-safe** — UTF-8 console bootstrap on Windows, latin-1 fallback when reading files

---

## 📋 Prerequisites

- **Python 3.10+**
- **Ollama** running locally — get it at [ollama.com/download](https://ollama.com/download)
- **~5 GB free disk** for a code-specialized model
- **Internet** for the first run (pulling the model)

> **Strongly recommended:** use a code-specialized model (`deepseek-coder`, `qwen2.5-coder`, `codellama`). General chat models are noticeably worse at preserving Python semantics during refactoring.

---

## 🚀 Quick start

### 1. Install Ollama and pull a model

```bash
ollama pull deepseek-coder        # or qwen2.5-coder, codellama
ollama serve
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Run the agent

**Interactive mode:**

```bash
python agent.py
> /path/to/your_script.py
> /path/to/some_project/      # processes all .py files recursively
```

**One-shot CLI:**

```bash
python agent.py /path/to/script.py
python agent.py /path/to/project/
```

**Commands inside the REPL:**

| Command | Effect |
|---------|--------|
| `/help` | Show usage |
| `/recursive on` / `/recursive off` | Toggle recursive directory traversal |
| `exit` / `quit` | Leave the agent |

---

## 📊 Output

For each input file `script.py`, the agent writes two files alongside the original:

- `script_refactored.py` — the cleaned-up code
- `script_refactored_report.md` — markdown report including:
  - line/character metrics, source language detected, AST validity
  - per-drafter AST status table (which drafters produced valid Python)
  - applied transformations
  - unified diff preview

Already-refactored files (those containing the `output_suffix` in their name) are automatically skipped in batch mode.

---

## ⚙️ Configuration

Edit `config.py` to tune behavior:

| Field | Purpose | Default |
|-------|---------|---------|
| `ollama_model` | Default model (exact match preferred, then prefix, then substring) | `deepseek-coder` |
| `ollama_base_url` | Ollama server URL | `http://127.0.0.1:11434` |
| `ollama_model_*` | Per-agent model overrides — empty means use default | `""` |
| `temperature_*` | Per-agent temperature; all conservative (0.0–0.1) | see file |
| `aggregator_ast_retries` | Aggregator retries when output fails AST | `1` |
| `output_suffix` / `report_suffix` | Naming for output files | `_refactored` / `_report` |
| `supported_extensions` | File extensions processed | `(".py",)` |
| `max_file_size_kb` | Skip files larger than this | `500` |
| `max_code_chars` | Truncation cap when sending code to the LLM | `12000` |
| `translate_to_english` / `enforce_pep8` / `add_type_hints` / `add_docstrings` | Toggle individual transformations | `True` |
| `validate_syntax` | AST validation of output (with fallback) | `True` |
| `prompts_dir` | Where versioned prompt files live | `./prompts` |

### Recommended models

| Model | Best for |
|-------|----------|
| `deepseek-coder` (or `:6.7b`, `:33b`) | Code-focused, very capable |
| `qwen2.5-coder` | Strong on code and explanation, multilingual |
| `codellama` | Solid baseline, good at Python idioms |

**Hybrid setup:** keep a fast model on the drafters and set `ollama_model_aggregator` to a larger model — the aggregator does the most demanding step (merging three drafts while preserving behavior).

### Editing prompts

Prompts live as plain text files in `./prompts/` (`01_translator.txt`, `02_refactorer.txt`, etc.). Edit them directly; they're versioned by your VCS like any other source. The agent loads them at startup.

---

## ⚠️ Important notes

- **Behavior preservation is best-effort, not guaranteed.** Always run your test suite against the refactored output before deploying.
- The agent **never** modifies the original file — outputs are always written to a new `_refactored.py` file.
- Lower temperatures = more reliable output. The defaults (0.0–0.1) are deliberate — code generation should not be creative.
- The aggregator deliberately uses **plain-text generation** (not `format=json`) because forcing JSON encoding around source code corrupts quotes and backslashes.

---

## 🔐 Privacy model

- ✅ Your source code **never** leaves your machine — all model calls go to local Ollama
- ✅ No telemetry, no analytics, no API keys required
- ✅ The Ollama instance runs locally; you control which models are pulled and used

---

## 📁 Project structure

```
pyrefactor-agent/
├── agent.py            # PythonRefactorAgent class, graph nodes, CLI
├── config.py           # Config dataclass with refactoring options and LLM settings
├── schemas.py          # Pydantic schemas for drafter outputs and batch results
├── io_utils.py         # Code fence stripping, language detection, AST validation
├── llm_client.py       # Unified Ollama client (text mode — no JSON wrapping)
├── prompts/
│   ├── 01_translator.txt
│   ├── 02_refactorer.txt
│   ├── 03_documenter.txt
│   └── 04_aggregator.txt
├── requirements.txt
└── README.md
```

---

## 🛣️ Roadmap

- [ ] Pre/post-refactor diff visualizer in the terminal
- [ ] Optional `pytest` execution after refactoring as a sanity check
- [ ] Custom rules per file type (Django, FastAPI, data science)
- [ ] Pip-installable package
- [ ] VS Code extension

---

## 📜 License

MIT — see `LICENSE` file.

## 👤 Author

**Cassiano Ribeiro Carneiro** — [@cassianorcarneiro](https://github.com/cassianorcarneiro)

---

### 🤖 AI Assistance Disclosure

The codebase architecture, organizational structure, and stylistic formatting of this repository were refactored and optimized leveraging [Claude](https://www.anthropic.com/claude) by Anthropic. All core business logic and intellectual property remain the work of the repository authors and are governed by the project's license.

---

> *Three agents that translate, refactor, and document — so you can focus on the logic.*
