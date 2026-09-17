# Local File Agent

A small, fully offline agent — similar in shape to Claude Code — that uses a
**local model running through [Ollama](https://ollama.com)** to read and
analyze files, write new files, create folders, rename/move/copy things,
and run shell commands, all inside one sandboxed workspace directory.

Nothing here calls Anthropic's API or any cloud service. The model, the
tool loop, and the files it touches all stay on your machine.

## How it compares to Claude Code

| | Claude Code | This project |
|---|---|---|
| Brain | Claude (hosted API) | Any local Ollama model that supports tool calling |
| Tool loop | Built-in, many tools, extended thinking, subagents | One straightforward loop, 11 tools |
| Sandboxing | Project directory + permission prompts | Same idea: one workspace root + confirmation prompts |
| Reliability | Very high — Claude is trained hard on tool use | Depends heavily on which local model you pick |

The architecture is the same "agentic loop": give the model a system prompt
and a list of tools, let it decide when to call them, execute the calls,
feed results back, repeat until it just talks instead of calling a tool.
The gap you'll actually feel is model quality — local open models are far
more likely than Claude to call a tool with malformed arguments, forget to
call a tool and hallucinate a file's contents instead, or loop. The
confirmation prompts and the iteration cap in `config.py` exist specifically
to contain that.

## Project layout

```
local-file-agent/
├── agent.py        # the REPL + the tool-calling loop itself
├── tools.py         # the 11 tools, their JSON schemas, and the sandbox guard
├── config.py         # workspace root, model name, auto-approve, limits
└── requirements.txt
```

## Setup

1. **Install Ollama** (the local model runtime): https://ollama.com/download

2. **Pull a model that supports tool calling.** Not all local models do —
   check the current list at https://ollama.com/search?c=tools before
   picking one. As of this writing, models good at agentic/tool-calling
   work include things like `qwen3.8`, `glm-5.3` / `glm-5.3-flash`, and
   `granite4.1`, but this list moves fast — new releases regularly leapfrog
   old ones, so check the tools page rather than trusting any name printed
   here. Pick the largest one your RAM/VRAM can comfortably hold; smaller
   (≤7B) models are noticeably worse at formatting tool calls correctly.

   ```bash
   ollama pull qwen3:4b     # example — swap for whatever you land on
   ```

3. **Install the Python dependency:**

   ```bash
   cd local-file-agent
   pip install -r requirements.txt
   ```

4. **Run it**, pointed at whatever folder you want the agent to work in:

   ```bash
   AGENT_WORKSPACE=/path/to/your/project AGENT_MODEL=qwen3:4b python agent.py
   ```

   If you omit `AGENT_WORKSPACE`, it uses the current directory. If you
   omit `AGENT_MODEL`, it defaults to `qwen3:4b` (edit the default in
   `config.py` if you'd rather not pass it every time).

5. Talk to it:

   ```
   you> list everything in this project, then summarize what src/ contains
   you> create a folder called drafts and move all .md files from notes/ into it
   you> read config.yaml and change the port from 8080 to 9090
   you> run the test suite and tell me what fails
   ```

   Anything that changes disk (writing, editing, moving, copying, deleting,
   running a command) will pause and show you exactly what it's about to do
   before it does it — type `y` to allow it, anything else to decline.

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `AGENT_WORKSPACE` | current directory | The one folder the agent is allowed to touch. All paths are sandboxed to this. |
| `AGENT_MODEL` | `qwen3:4b` | Which pulled Ollama model to drive the agent. |
| `AGENT_AUTO_APPROVE` | `0` | Set to `1` to skip all confirmation prompts — same idea as Claude Code's "accept all edits" mode. Only use this once you trust the setup. |
| `AGENT_MAX_ITERATIONS` | `15` | Max tool-call round-trips per turn, so a confused model can't loop forever. |
| `AGENT_COMMAND_TIMEOUT` | `60` | Seconds before a shell command run by the agent is killed. |

## The tools

All defined in `tools.py`, all path arguments resolved and checked against
`AGENT_WORKSPACE` before anything happens (a path that would escape the
workspace, e.g. `../../etc/passwd`, is refused outright, not just warned
about):

- `list_directory(path)` — list a folder's contents
- `read_file(path, start_line, end_line)` — read a file, with line numbers
- `glob_files(pattern, path)` — find files by name pattern (`**/*.py`)
- `search_files(pattern, path, file_glob)` — regex content search across files (like grep)
- `write_file(path, content)` — create or overwrite a file *(confirmed)*
- `edit_file(path, old_string, new_string)` — exact find-and-replace inside a file, shows a diff before applying *(confirmed)*
- `create_directory(path)` — make a new folder *(confirmed)*
- `move_file(source, destination)` — move or rename *(confirmed)*
- `copy_file(source, destination)` — copy a file or folder *(confirmed)*
- `delete_path(path)` — permanently delete a file or folder *(confirmed, irreversible)*
- `run_command(command, cwd)` — run a shell command inside the workspace *(confirmed, time-limited)*

*(confirmed)* = asks you before doing it, unless `AGENT_AUTO_APPROVE=1`.

## Extending it with a new tool

1. Write a plain function in `tools.py` that takes simple arguments
   (strings, ints) and returns a string describing what happened. Route any
   path through `resolve_path()` first, and call `confirm(...)` before
   anything destructive.
2. Add it to the `TOOL_FUNCTIONS` dict.
3. Add a matching JSON-schema entry to `TOOL_SCHEMAS` (name, description,
   parameters) — this is the only part the model actually "sees", so make
   the description clear about what the tool does and when to use it.

That's the whole extension mechanism — there's no plugin system to learn.

## Why tool calls sometimes fail (and what to do)

Local models are much less consistent at tool calling than Claude is. If
you see the agent looping, calling tools with garbage arguments, or just
narrating what it *would* do instead of calling a tool:

- Try a bigger model, or one explicitly tuned for "agentic"/tool tasks
  (check https://ollama.com/search?c=tools).
- Keep instructions concrete and scoped ("rename `a.txt` to `b.txt`" beats
  "clean up this mess").
- Lower `AGENT_MAX_ITERATIONS` while testing a new model so a bad loop
  fails fast instead of burning minutes.
- If you outgrow local models' reliability for this, the same "system
  prompt + tools + confirmation loop" architecture is exactly what the
  [Claude Agent SDK](https://docs.claude.com/en/api/agent-sdk/overview)
  gives you against Claude itself, if you ever want to swap the brain
  without redesigning the tools.

## Safety notes

- The sandbox only stops the agent from reading/writing *outside* the
  workspace folder you point it at — it does not stop it from doing
  something destructive *inside* that folder if you approve the action.
  Point it at a folder you're comfortable letting it modify, and consider
  using it inside a git repo so you can diff/revert changes.
- `run_command` executes real shell commands with your user's permissions.
  Treat the confirmation prompt as your last line of defense, and read the
  command before approving it — the same discipline Claude Code expects
  from you when it asks to run something.
- `AGENT_AUTO_APPROVE=1` removes every safety prompt. Use it only in
  throwaway sandboxes or once you've watched the agent behave well with
  confirmations on.
