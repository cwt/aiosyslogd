#!/bin/bash

rm -rf */__pycache__ .pytest_cache
poetry update
poetry install -E speed -E gemini
poetry run pytest --cov=aiosyslogd --cov-report=term-missing

