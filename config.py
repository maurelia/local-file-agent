"""
Central configuration for the local file agent.

Everything here can be overridden with an environment variable so you can
run multiple agents (different models, different sandboxed folders) without
touching code.
"""
import os
from pathlib import Path

# The ONE folder this agent is allowed to touch. Every file tool resolves
# paths against this root and refuses to go outside it (see tools.py:
# resolve_path). This is what makes it safe to say "clean up this project"
# without worrying the model wanders into your home directory.
WORKSPACE_ROOT = Path(
    os.environ.get("AGENT_WORKSPACE", os.getcwd())
).resolve()

# Which local model to drive the agent with. Must be a model you've pulled
# with `ollama pull <name>` AND that supports tool calling (check
# https://ollama.com/search?c=tools — the list changes as new models ship).
# Pick the largest one your VRAM/RAM can hold; small models frequently emit
# malformed tool calls or forget to call tools at all.
MODEL = os.environ.get("AGENT_MODEL", "qwen3:4b")

# If "1"/"true", the agent executes write_file / edit_file / move_file /
# copy_file / delete_path / run_command WITHOUT asking you first. Leave
# this off until you trust the setup — it's the equivalent of running
# Claude Code with all permissions pre-approved.
AUTO_APPROVE = os.environ.get("AGENT_AUTO_APPROVE", "0").lower() in ("1", "true", "yes")

# Safety valve: max tool-calling round-trips per single user turn, so a
# confused model can't loop forever burning tokens/CPU.
MAX_TOOL_ITERATIONS = int(os.environ.get("AGENT_MAX_ITERATIONS", "15"))

# Timeout (seconds) for any shell command the agent runs.
COMMAND_TIMEOUT = int(os.environ.get("AGENT_COMMAND_TIMEOUT", "60"))
