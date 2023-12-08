#!/usr/bin/env bash
# (C) 2023–present Bartosz Sławecki (bswck)
#
# Run this before pushing to ensure that the package lock is up-to-date.
#
# This file was generated from bswck/skeleton@3e18832.
# Instead of changing this particular file, you might want to alter the template:
# https://github.com/bswck/skeleton/tree/3e18832/project/scripts/lock.sh.jinja
#
# Usage:
# $ poe lock

poetry lock --no-update
echo "Auto-commit package lock"
git add "poetry.lock" && git commit -m "Update package lock" || exit 0