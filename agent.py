#!/usr/bin/env python3
"""
A minimal, Claude-Code-style coding/file agent running entirely on a local
model via Ollama.

Loop, in plain words:
  1. You type an instruction.
  2. The model sees the conversation + the list of available tools and
     either answers directly, or asks to call one or more tools.
  3. If it calls tools, we actually run them (with a confirmation prompt
     for anything that touches disk) and feed the results back to the
     model as new messages.
  4. Repeat step 2-3 until the model responds with plain text instead of a
     tool call, then print that and wait for your next instruction.

This is the same "agentic loop" shape Claude Code itself uses — the only
difference is the brain (a local Ollama model instead of Claude) and a much
smaller tool set.

Usage:
    python agent.py                # interactive REPL in the current directory
    AGENT_WORKSPACE=/path python agent.py
    AGENT_MODEL=glm-5.3 python agent.py
"""
import sys

import ollama

import config
from tools import TOOL_FUNCTIONS, TOOL_SCHEMAS

SYSTEM_PROMPT = f"""You are a local coding and file-management assistant, \
similar to Claude Code, running entirely offline.

You have tools to inspect, create, edit, move, copy, and delete files and \
directories, and to run shell commands — all restricted to this workspace: \
{config.WORKSPACE_ROOT}

Rules:
- Never guess at file contents or directory structure — use list_directory, \
read_file, glob_files, or search_files to check first.
- Prefer edit_file for small changes to existing files, and write_file only \
for new files or full rewrites.
- Explain briefly what you're about to do before doing something \
destructive (delete_path, overwriting a file, running a command).
- If a tool result contains "ERROR" or "DECLINED", don't retry the same \
call blindly — explain the problem to the user or try a different approach.
- Keep your final answers concise and concrete: what you did, and what the \
user should check.
"""


def run_turn(messages):
    """Run the tool-calling loop for one user turn. Mutates `messages` in
    place and returns the final assistant text."""
    for _ in range(config.MAX_TOOL_ITERATIONS):
        response = ollama.chat(model=config.MODEL, messages=messages, tools=TOOL_SCHEMAS)
        msg = response["message"]
        messages.append(msg)

        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            return msg.get("content", "")

        for call in tool_calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"] or {}
            func = TOOL_FUNCTIONS.get(name)

            if func is None:
                result = f"ERROR: unknown tool '{name}'."
            else:
                print(f"\n  [tool] {name}({', '.join(f'{k}={v!r}' for k, v in args.items())})")
                try:
                    result = func(**args)
                except Exception as e:
                    result = f"ERROR: tool raised an exception: {e}"

            messages.append({"role": "tool", "content": str(result), "tool_name": name})

    return ("(Stopped after hitting the tool-call limit — the model may be "
            "stuck in a loop. Try rephrasing your request.)")


def main():
    print(f"Local file agent — model: {config.MODEL} | workspace: {config.WORKSPACE_ROOT}")
    print(f"Auto-approve: {'ON (no confirmations)' if config.AUTO_APPROVE else 'off'}")
    print("Type your instruction, or 'exit' to quit.\n")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            break

        messages.append({"role": "user", "content": user_input})
        try:
            reply = run_turn(messages)
        except Exception as e:
            print(f"\n[error] {e}\n"
                  f"Is Ollama running, and have you pulled '{config.MODEL}'? "
                  f"Try: ollama pull {config.MODEL}")
            messages.pop()  # drop the user message so it isn't sent twice
            continue

        print(f"\nagent> {reply}\n")


if __name__ == "__main__":
    sys.exit(main())
