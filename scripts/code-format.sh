#!/bin/bash

poetry run black -t py312 -l 80 aiosyslogd/*.py
poetry run black -t py312 -l 80 aiosyslogd/*/*.py
poetry run black -t py312 -l 80 tests/*.py

# Remove trailing whitespace in all .py files
find . -name "*.py" -exec sed -i 's/[[:space:]]*$//' {} \;
