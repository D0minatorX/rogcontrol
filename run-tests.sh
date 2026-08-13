#!/bin/bash
# Stdlib unittest only - the project deliberately has no test dependencies.
set -e
cd "$(dirname "$0")"
python3 -m unittest discover -s tests -v
