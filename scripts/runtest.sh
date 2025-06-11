#!/bin/bash

rm -rf */__pycache__ .pytest_cache
poetry update
poetry install
poetry run pytest --cov=aiosyslogd --cov-report=term-missing

