# 🐍 PyRefactor Agent

> Multi-agent Python refactoring tool powered by local LLMs.

A privacy-first, offline-capable Python refactoring system that translates, refactors, and documents Python code via three specialized agents — with AST-validated output and zero data sharing with proprietary model providers.

<p align="center">
  <img alt="Stack" src="https://img.shields.io/badge/Stack-LangGraph%20%2B%20Ollama-blue?style=for-the-badge">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge">
  <img alt="Status" src="https://img.shields.io/badge/Privacy-Local%20First-success?style=for-the-badge">
</p>

---

## 📦 How it works

The agent runs **three specialists in parallel** on every input file, then merges their work:

| Agent | Responsibility |
|-------|----------------|
| **Translator** | Converts identifiers, comments, docstrings, and user-facing strings to English |
| **Refactorer** | Applies PEP 8, adds type hints, modernizes idioms, removes dead code |
| **Documenter** | Adds Google-style docstrings, section dividers, and useful inline comments |
| **Aggregator** | Merges the three drafts into one final version (correctness > clarity > brevity) |

The output is **AST-validated** — if aggregation produces invalid Python, the agent automatically falls back to the refactorer draft.

---

## 📋 Prerequisites

- **Python 3.10+**
- **Ollama** running locally — get it at [ollama.com/download](https://ollama.com/download)
- **~5 GB free disk** (for a code-specialized model)
- **Internet** for the first run (pulling the model)

---

## 🚀 Quick start

### 1. Install Ollama and pull a model

```bash
ollama pull deepseek-coder        # or qwen2.5-coder, codellama
ollama serve
```

### 2. Install Python dependencies

```bash
pip install langgraph langchain-ollama ollama rich pydantic
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

- `/help` — show usage
- `/recursive on|off` — toggle recursive directory traversal
- `exit` or `quit` — leave

---

## 📊 Output

For each input file `script.py`, the agent writes two files alongside it:

- `script_refactored.py` — the cleaned-up code
- `script_refactored_report.md` — markdown report with metrics, applied transformations, and a unified diff preview

---

## ⚙️ Configuration

Edit `config.py` to change:

- `ollama_model` — which local model to use
- `temperature_*` — determinism levels (kept low by default — code generation should not be creative)
- `max_file_size_kb` — skip files above this size
- `max_code_chars` — truncate very long files for the LLM context
- Toggle individual transformations: `translate_to_english`, `enforce_pep8`, `add_type_hints`, `add_docstrings`

---

## ⚠️ Important notes

- **Behavior preservation is best-effort, not guaranteed.** Always run your test suite against the refactored output before deploying.
- For best results on Python code, use a code-specialized model (`deepseek-coder`, `qwen2.5-coder`, `codellama`). General chat models are noticeably worse at this task.
- Lower temperatures = more reliable output. The defaults (0.0–0.1) are deliberate.

---

## 📁 Project structure

```
pyrefactor-agent/
├── agent.py        # PythonRefactorAgent class, graph nodes, CLI
├── config.py       # Refactoring options and LLM settings
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
