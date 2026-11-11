# Contributing

1. Fork the repository and create a focused branch.
2. Install development dependencies with `python -m pip install -e ".[dev]"`.
3. Run `python -m pytest -q` before opening a pull request.
4. Keep site-specific logic in profiles/rules where possible rather than hardcoding it into the core pipeline.
5. Do not commit generated captures, SQLite project files, credentials, build output, or release archives.
