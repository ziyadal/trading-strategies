# Repository Guidelines

## Project Structure & Module Organization
The active project lives in `QC/`. Treat the workspace root as a container and do strategy work inside that directory. Key files:

- `QC/long_rvol_9ema_framework_annotated.py`: main QuantConnect/LEAN algorithm and framework models.
- `QC/v1_Strat.ipynb`: research notebook for experiments and idea validation.
- `QC/pricing estimate.md`: sizing and infrastructure notes.

There is no dedicated `tests/` package yet. If you add reusable logic, prefer extracting it from the algorithm file and placing tests under `QC/tests/`.

## Build, Test, and Development Commands
Run commands from `QC/` unless noted otherwise.

- `python -m py_compile long_rvol_9ema_framework_annotated.py`: fast syntax check before every commit.
- `jupyter lab v1_Strat.ipynb`: open the research notebook locally.
- `lean backtest`: run a local LEAN backtest if you have a personal `lean.json` and CLI setup.

There is no repo-level build pipeline today, so validation is mainly syntax checks plus backtest results.

## Coding Style & Naming Conventions
Use Python with 4-space indentation, type hints, and small focused methods. Match the existing naming style:

- `PascalCase` for algorithm, alpha, universe, and state classes.
- `snake_case` for methods and variables.
- Prefix internal fields with `_` when they are implementation details.

Keep comments short and decision-oriented. Explain trading or risk logic, not obvious syntax.

## Testing Guidelines
This project currently relies on backtesting more than unit tests. Before opening a PR:

- run `python -m py_compile long_rvol_9ema_framework_annotated.py`
- run at least one backtest covering the current strategy window
- note any changes to universe size, risk limits, or exit behavior

For new tests, use `test_<feature>.py` names under `QC/tests/` and isolate pure logic from QuantConnect APIs where possible.

## Commit & Pull Request Guidelines
The existing `QC/` history is minimal and not a reliable style guide, so use clear imperative commit subjects such as `Refine RVOL scan thresholds`. Keep the subject concise and explain strategy impact in the body when behavior changes.

PRs should include a short summary, validation steps, and updated backtest evidence for trading-logic changes. Attach charts or screenshots when entry, exit, or portfolio behavior changes materially.

## Security & Configuration Tips
Do not commit credentials, personal `lean.json`, cached data, `__pycache__/`, or notebook checkpoint files. Keep environment-specific settings local and out of version control.
