# Testing with Pytest and UV

This project uses `uv` for environment management and `pytest` for running tests.

## Running Tests

To run all tests in the project, use:
```powershell
uv run pytest
```

To run a specific test file:
```powershell
uv run pytest tests_tmp/test_llm_inference.py
```

## Configuration (pyproject.toml)

The `pytest` behavior is configured in the `[tool.pytest.ini_options]` section of `pyproject.toml`.

### 1. Module Discovery (pythonpath)
We added `pythonpath = ["."]` to resolve `ModuleNotFoundError: No module named 'agentguard'`.
- This ensures that the root directory is added to Python's search path.
- It allows tests to import the project package using `from agentguard...`.

### 2. Console Output (addopts = "-s")
By default, `pytest` captures console output and only shows it on failure.
- We added `addopts = "-s"` (or `--capture=no`) to ensure that `print()` statements and logging are always visible in the terminal during test execution.

### 3. Test Paths
We explicitly set `testpaths = ["tests_tmp"]` to tell `pytest` where to look for tests by default.

## Summary of pyproject.toml section:
```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests_tmp"]
addopts = "-s"
```
