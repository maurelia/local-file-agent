"""
The agent's tools: everything it can do to the filesystem, plus a sandboxed
shell. Each tool is a plain Python function (easy to test on its own from a
REPL) plus a JSON-schema entry that gets handed to the model so it knows the
tool exists and how to call it.

Design choices worth knowing about:

- Every path argument goes through resolve_path(), which refuses anything
  that would land outside config.WORKSPACE_ROOT. This is the same idea as
  Claude Code restricting itself to the current project directory.
- Anything that changes disk state (write_file, edit_file, move_file,
  copy_file, delete_path) or runs a command goes through confirm() first,
  unless AUTO_APPROVE is on. This is your permission prompt, same role as
  Claude Code asking "Do you want to make this edit?".
- Every tool returns a small dict/string, never raises on "expected" failure
  modes (file not found, path escape, user declined) — it returns an error
  string instead, so the model can see what went wrong and try something
  else instead of the whole turn crashing.
"""
import difflib
import fnmatch
import os
import re
import shutil
import subprocess
from pathlib import Path

import config


class PathEscapeError(Exception):
    pass


def resolve_path(user_path: str) -> Path:
    """Resolve a path the model gives us against the sandbox root and make
    sure it doesn't escape it (blocks '../../etc/passwd' style tricks)."""
    candidate = (config.WORKSPACE_ROOT / user_path).resolve()
    try:
        candidate.relative_to(config.WORKSPACE_ROOT)
    except ValueError:
        raise PathEscapeError(
            f"Refusing to touch '{user_path}': it resolves outside the "
            f"sandboxed workspace ({config.WORKSPACE_ROOT})."
        )
    return candidate


def confirm(description: str) -> bool:
    """Ask the human before doing something destructive. Skipped entirely
    when AUTO_APPROVE is set."""
    if config.AUTO_APPROVE:
        return True
    answer = input(f"\n  >> {description}\n     Proceed? [y/N] ").strip().lower()
    return answer in ("y", "yes")


# ---------------------------------------------------------------------------
# Read-only tools (never need confirmation)
# ---------------------------------------------------------------------------

def list_directory(path: str = ".") -> str:
    try:
        target = resolve_path(path)
    except PathEscapeError as e:
        return f"ERROR: {e}"
    if not target.exists():
        return f"ERROR: '{path}' does not exist."
    if not target.is_dir():
        return f"ERROR: '{path}' is not a directory."
    entries = []
    for item in sorted(target.iterdir()):
        kind = "DIR " if item.is_dir() else "FILE"
        size = "" if item.is_dir() else f" ({item.stat().st_size}B)"
        entries.append(f"{kind}  {item.name}{size}")
    return "\n".join(entries) if entries else "(empty directory)"


def read_file(path: str, start_line: int = None, end_line: int = None) -> str:
    try:
        target = resolve_path(path)
    except PathEscapeError as e:
        return f"ERROR: {e}"
    if not target.exists():
        return f"ERROR: '{path}' does not exist."
    if not target.is_file():
        return f"ERROR: '{path}' is not a file."
    try:
        lines = target.read_text(errors="replace").splitlines()
    except Exception as e:
        return f"ERROR reading '{path}': {e}"
    start = (start_line - 1) if start_line else 0
    end = end_line if end_line else len(lines)
    numbered = [f"{i + start + 1}\t{line}" for i, line in enumerate(lines[start:end])]
    return "\n".join(numbered) if numbered else "(empty file)"


def glob_files(pattern: str, path: str = ".") -> str:
    """Find files by name pattern, e.g. pattern='**/*.py'."""
    try:
        target = resolve_path(path)
    except PathEscapeError as e:
        return f"ERROR: {e}"
    matches = sorted(str(p.relative_to(config.WORKSPACE_ROOT)) for p in target.glob(pattern))
    return "\n".join(matches) if matches else "(no matches)"


def search_files(pattern: str, path: str = ".", file_glob: str = "*") -> str:
    """Grep-like content search: regex `pattern` across files under `path`
    matching `file_glob` (e.g. '*.py')."""
    try:
        target = resolve_path(path)
    except PathEscapeError as e:
        return f"ERROR: {e}"
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"ERROR: invalid regex: {e}"

    results = []
    for filepath in target.rglob("*"):
        if not filepath.is_file() or not fnmatch.fnmatch(filepath.name, file_glob):
            continue
        try:
            text = filepath.read_text(errors="ignore")
        except Exception:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                rel = filepath.relative_to(config.WORKSPACE_ROOT)
                results.append(f"{rel}:{lineno}: {line.strip()}")
        if len(results) > 200:
            results.append("... (truncated at 200 matches)")
            break
    return "\n".join(results) if results else "(no matches)"


# ---------------------------------------------------------------------------
# Write tools (confirmation-gated)
# ---------------------------------------------------------------------------

def write_file(path: str, content: str) -> str:
    try:
        target = resolve_path(path)
    except PathEscapeError as e:
        return f"ERROR: {e}"
    action = "Overwrite" if target.exists() else "Create"
    if not confirm(f"{action} file '{path}' ({len(content)} chars)"):
        return "DECLINED: user did not approve this write."
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return f"OK: wrote {len(content)} chars to '{path}'."


def edit_file(path: str, old_string: str, new_string: str) -> str:
    """Replace an exact substring in a file, like a find-and-replace. Fails
    loudly if old_string isn't found or isn't unique, so the model doesn't
    silently edit the wrong spot."""
    try:
        target = resolve_path(path)
    except PathEscapeError as e:
        return f"ERROR: {e}"
    if not target.is_file():
        return f"ERROR: '{path}' is not a file."
    original = target.read_text()
    count = original.count(old_string)
    if count == 0:
        return "ERROR: old_string not found in file. Nothing changed."
    if count > 1:
        return f"ERROR: old_string appears {count} times; make it more specific so it matches exactly once."

    updated = original.replace(old_string, new_string, 1)
    diff = "\n".join(
        difflib.unified_diff(
            original.splitlines(), updated.splitlines(),
            fromfile=path, tofile=path, lineterm="",
        )
    )
    if not confirm(f"Edit '{path}':\n{diff}"):
        return "DECLINED: user did not approve this edit."
    target.write_text(updated)
    return f"OK: edited '{path}'."


def create_directory(path: str) -> str:
    try:
        target = resolve_path(path)
    except PathEscapeError as e:
        return f"ERROR: {e}"
    if target.exists():
        return f"ERROR: '{path}' already exists."
    if not confirm(f"Create directory '{path}'"):
        return "DECLINED: user did not approve this."
    target.mkdir(parents=True)
    return f"OK: created directory '{path}'."


def move_file(source: str, destination: str) -> str:
    """Move or rename a file/folder (same operation, like `mv`)."""
    try:
        src = resolve_path(source)
        dst = resolve_path(destination)
    except PathEscapeError as e:
        return f"ERROR: {e}"
    if not src.exists():
        return f"ERROR: source '{source}' does not exist."
    if dst.exists():
        return f"ERROR: destination '{destination}' already exists."
    if not confirm(f"Move/rename '{source}' -> '{destination}'"):
        return "DECLINED: user did not approve this."
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return f"OK: moved '{source}' to '{destination}'."


def copy_file(source: str, destination: str) -> str:
    try:
        src = resolve_path(source)
        dst = resolve_path(destination)
    except PathEscapeError as e:
        return f"ERROR: {e}"
    if not src.exists():
        return f"ERROR: source '{source}' does not exist."
    if dst.exists():
        return f"ERROR: destination '{destination}' already exists."
    if not confirm(f"Copy '{source}' -> '{destination}'"):
        return "DECLINED: user did not approve this."
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)
    return f"OK: copied '{source}' to '{destination}'."


def delete_path(path: str) -> str:
    """Delete a file or folder. Extra-explicit confirmation since this is
    the one irreversible tool."""
    try:
        target = resolve_path(path)
    except PathEscapeError as e:
        return f"ERROR: {e}"
    if not target.exists():
        return f"ERROR: '{path}' does not exist."
    kind = "directory (recursively)" if target.is_dir() else "file"
    if not confirm(f"PERMANENTLY DELETE {kind} '{path}'"):
        return "DECLINED: user did not approve this deletion."
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return f"OK: deleted '{path}'."


def run_command(command: str, cwd: str = ".") -> str:
    """Run a shell command inside the sandboxed workspace. Always confirmed
    first, always time-limited, output is truncated so one runaway command
    can't blow up the context window."""
    try:
        workdir = resolve_path(cwd)
    except PathEscapeError as e:
        return f"ERROR: {e}"
    if not confirm(f"Run shell command in '{cwd}':\n     $ {command}"):
        return "DECLINED: user did not approve running this command."
    try:
        result = subprocess.run(
            command, shell=True, cwd=workdir,
            capture_output=True, text=True, timeout=config.COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {config.COMMAND_TIMEOUT}s."
    output = (result.stdout or "") + (result.stderr or "")
    if len(output) > 4000:
        output = output[:4000] + "\n... (truncated)"
    return f"exit_code={result.returncode}\n{output}".strip()


# ---------------------------------------------------------------------------
# Wiring: schemas the model sees, and the dispatch table the loop calls
# ---------------------------------------------------------------------------

TOOL_FUNCTIONS = {
    "list_directory": list_directory,
    "read_file": read_file,
    "glob_files": glob_files,
    "search_files": search_files,
    "write_file": write_file,
    "edit_file": edit_file,
    "create_directory": create_directory,
    "move_file": move_file,
    "copy_file": copy_file,
    "delete_path": delete_path,
    "run_command": run_command,
}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and subdirectories at a path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory to list, relative to the workspace root. Defaults to '.'"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file's contents, with line numbers. Optionally read only a line range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File to read, relative to the workspace root."},
                    "start_line": {"type": "integer", "description": "1-indexed first line to read (optional)."},
                    "end_line": {"type": "integer", "description": "1-indexed last line to read, inclusive (optional)."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob_files",
            "description": "Find files by name/glob pattern, e.g. '**/*.py' for all Python files recursively.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.md'."},
                    "path": {"type": "string", "description": "Directory to search under. Defaults to '.'"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search file contents with a regex pattern across a directory tree (like grep).",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regular expression to search for."},
                    "path": {"type": "string", "description": "Directory to search under. Defaults to '.'"},
                    "file_glob": {"type": "string", "description": "Only search files matching this glob, e.g. '*.py'. Defaults to '*'."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new file or overwrite an existing one with the given content. Creates parent directories as needed. Asks for user confirmation first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File to write, relative to the workspace root."},
                    "content": {"type": "string", "description": "Full contents to write to the file."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace one exact occurrence of old_string with new_string inside an existing file. old_string must match exactly once. Asks for user confirmation first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File to edit, relative to the workspace root."},
                    "old_string": {"type": "string", "description": "Exact text to find (must be unique in the file)."},
                    "new_string": {"type": "string", "description": "Text to replace it with."},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_directory",
            "description": "Create a new directory (and any missing parent directories). Asks for user confirmation first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path to create, relative to the workspace root."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_file",
            "description": "Move or rename a file or directory. Asks for user confirmation first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Existing path, relative to the workspace root."},
                    "destination": {"type": "string", "description": "New path, relative to the workspace root."},
                },
                "required": ["source", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "copy_file",
            "description": "Copy a file or directory to a new location. Asks for user confirmation first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Existing path, relative to the workspace root."},
                    "destination": {"type": "string", "description": "New path, relative to the workspace root."},
                },
                "required": ["source", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_path",
            "description": "Permanently delete a file or directory (recursively). Asks for explicit user confirmation first — irreversible.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to delete, relative to the workspace root."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command inside the sandboxed workspace (e.g. to run tests, install a package, or run a script). Asks for user confirmation first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute."},
                    "cwd": {"type": "string", "description": "Working directory to run it in, relative to the workspace root. Defaults to '.'"},
                },
                "required": ["command"],
            },
        },
    },
]
