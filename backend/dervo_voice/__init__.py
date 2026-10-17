"""Dervo's voice for Home: its own process, supervised by the app (src/voice.rs), outside the host's request queue.

Run ``python -m dervo_voice --help``. Design: /mnt/project-files/dervo/agents-and-assistant.md, "Always-on voice for
Home". The loop is loop.py, the models engines.py, the record of every utterance journal.py.
"""
