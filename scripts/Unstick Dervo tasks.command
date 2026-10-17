#!/bin/bash
# Marks runs Dervo lost track of as stopped, for a workspace you can't open. Quit Dervo first.
# Your data is backed up before anything changes. Dervo also has a Mark as stopped button in Progress.
cd "$(dirname "$0")" || exit 1
exec /usr/bin/python3 "./unstick_dervo_tasks.py"
