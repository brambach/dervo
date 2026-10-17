"""Smartness evals: Dervo's automatic decisions scored against the four reviews in notes/smartness/.

    python3 -m evals.runner --mode fake                 # every case through the fake CLI, no model, what CI runs
    python3 -m evals.runner --mode real --cap 60        # the baseline: real claude and Codex behind a hard cap

Real calls only ever go through headless_driver's real-model mode and evals/real_cli.py, which counts them.
"""
