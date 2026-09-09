# Repository Guidelines

## Project Structure & Module Organization
`main.py` is the repository's only source file and contains the full QuantConnect algorithm: universe selection, alpha generation, portfolio construction, and trade management. `algo.txt` is a plain-English strategy summary that should stay aligned with the implementation. `__pycache__/` is generated output and should not be edited or reviewed as source.

## Build, Test, and Development Commands
Use lightweight local checks in this repository:

- `python3 -m py_compile main.py` validates Python syntax without running the algorithm.
- `rg "pattern" main.py algo.txt` is the fastest way to inspect logic or parameter usage.

Backtests require a QuantConnect/LEAN environment that provides `AlgorithmImports`. If you copy this file into a configured LEAN project, run your normal backtest flow there, for example `lean backtest`.

## Coding Style & Naming Conventions
Follow PEP 8 with 4-space indentation and keep type hints on public methods and state containers. Match the existing naming patterns: `PascalCase` for classes, `snake_case` for methods and fields, and descriptive model names such as `TwoMinuteEmaSupportAlphaModel`. Keep strategy thresholds explicit and close to the logic they affect; if a value is non-obvious, add a short comment instead of a long block comment.

## Testing Guidelines
There is no committed test suite yet. Every change should at minimum pass `python3 -m py_compile main.py` and a backtest in QuantConnect/LEAN before review. When adding tests later, place them under `tests/`, name files `test_*.py`, and focus first on deterministic helpers such as volume-window calculations, ranking, and signal invalidation rules.

## Commit & Pull Request Guidelines
Current history uses short, imperative commit subjects like `Add QC strategy workspace` and `Remove project headers from README.md`. Keep commit titles concise, capitalized, and action-first; avoid mixing refactors and strategy changes in one commit.

Pull requests should explain the behavioral change, list any parameter updates, and include backtest evidence when trading logic changes. If the change affects entry, exit, sizing, or scheduling, mention the exact methods touched in `main.py`.
