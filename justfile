# Justfile for Python project automation

# Set shell and environment
set shell := ["bash", "-cu"]

# Default Python interpreter
PYTHON := "python3"
VENV_DIR := ".venv"
ACTIVATE := VENV_DIR + "/bin/activate"

help:
    just --list

# Setup virtual environment
setup:
    {{PYTHON}} -m venv {{VENV_DIR}}
    source {{ACTIVATE}} && pip install --upgrade pip setuptools wheel
    source {{ACTIVATE}} && pip install build twine
    source {{ACTIVATE}} && pip install .

# Install ruff if not already installed
install-ruff:
    source {{ACTIVATE}} && pip install ruff

# Run ruff format
format:
    source {{ACTIVATE}} && ruff format .

# Run ruff lint
lint:
    source {{ACTIVATE}} && ruff check .

typing:
    pytype

typing-merge:
    #!/bin/sh
    find .pytype/pyi/pkgmgr -name '*.pyi' | while read -r stub; do
        rel_path="${stub#*.pytype/pyi/pkgmgr/}"
        py_path="${rel_path%.pyi}"
        merge-pyi -i "pkgmgr/${py_path}.py" "$stub"
    done

# Run all checks
check: format lint typing

# Run tests
test:
    source {{ACTIVATE}} && pytest tests

# Build distribution
build:
    source {{ACTIVATE}} && python -m build

# Publish to PyPI (uses twine)
publish: clean build
    source {{ACTIVATE}} && twine upload dist/*

# Clean build artifacts
clean:
    rm -rf dist/ build/ *.egg-info .pytest_cache .ruff_cache

# Reinstall dev environment
reinstall:
    rm -rf {{VENV_DIR}}
    just setup

