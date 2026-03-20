# skillctl

`skillctl` is a wrapper for the `claude`, `codex`, and `gemini` CLIs. It keeps skill discovery local, lets you load a skill explicitly with `@skill_name`, and gives you one workflow across multiple CLIs.

## What It Solves

Tools like Claude Code, Codex, and Gemini CLI support user-defined **skills**, usually stored in directories such as `~/.codex/skills/` and `~/.claude/skills/`.

**The problem**: skill behavior differs across CLIs, and it is not always clear how much skill content reaches the model up front. If a CLI eagerly loads full skill bodies, token overhead can grow fast. If it already uses staged routing, the token savings from `skillctl` may be small.

**skillctl's approach**:

```text
skillctl: build a local skill index -> expose skill names for explicit loading ->
          inject a skill file only when the user asks for it with @skill_name
```

1. Create an isolated runtime with an empty skills directory while preserving auth and config
2. Build a lightweight local index from skill metadata
3. Inject a skill file only when the user types `@skill_name`
4. Report rough token estimates for what `skillctl` injected during the session

## In Practice

Tested with `codex` using `@brainstorming help me`:

- ✅ The TUI still works, including arrow keys, Tab, and Ctrl+C
- ✅ Codex receives and understands the injected skill content
- ✅ Codex follows the brainstorming skill workflow as expected
- ✅ Token statistics are printed when the session ends

What you get from this project:

- ✅ Explicit control over when a skill file is injected
- ✅ One wrapper and one indexing model across multiple CLIs
- ✅ Local suggestion mode based on skill metadata
- ✅ A rough count of what `skillctl` injected

Token savings are conditional:

- If a CLI would otherwise preload full skill bodies, `skillctl` can cut prompt overhead a lot.
- If a CLI already does staged discovery and on-demand loading, the token savings may be modest.
- In that case, the main value is control and consistency, not huge token savings.

## Installation

### Prerequisites

- macOS or Linux
- Python >= 3.10
- At least one of `claude`, `codex`, or `gemini` installed

### Option 1: One-Line Remote Install

```bash
curl -fsSL https://raw.githubusercontent.com/pseudoctor/skillctl/main/install.sh \
  | SKILLCTL_REPO_URL="https://github.com/pseudoctor/skillctl.git" sh
```

This command clones the repository, checks which CLIs are available, installs the Python package, rebuilds the skill index, and installs the shim.

### Option 2: Local Install

```bash
git clone https://github.com/pseudoctor/skillctl.git
cd skillctl
./install.sh
```

Optional parameters:

```bash
PYTHON_BIN=python3.12 SHIM_DIR="$HOME/.local/bin" ./install.sh
```

### Uninstall

```bash
./uninstall.sh                 # Keep the cache
REMOVE_CACHE=1 ./uninstall.sh  # Remove the cache too
```

Or uninstall remotely:

```bash
curl -fsSL https://raw.githubusercontent.com/pseudoctor/skillctl/main/uninstall.sh \
  | SKILLCTL_REPO_URL="https://github.com/pseudoctor/skillctl.git" sh
```

## Usage

### Shim Mode (Recommended)

After installing the shim, you can keep using the CLI as before:

```bash
# Install the shim
skillctl shim install

# Use the CLIs directly; skillctl sits in front of them
codex
claude
gemini
```

The shim installs to `~/.local/bin` by default. If that directory is not on your `PATH`, add it:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

### Direct Launch Mode

If you do not want the shim, run `skillctl` directly:

```bash
skillctl codex
skillctl claude
skillctl gemini

# Equivalent to
python3 -m skillctl codex
```

### Use `@skill_name` Inside a Session

Once the CLI is running, type as usual. When you want a skill, prefix it with `@`:

```text
@brainstorming help me evaluate whether this feature is worth building
```

When you press Enter, skillctl injects the selected skill file into the session. After that, you usually do not need to mention it again because the content is already in the conversation.

### Common Commands

```bash
# Manage the skill index
skillctl index rebuild          # Rebuild the index

# View skills
skillctl list                   # List all skills
skillctl list --global-only     # Show global skills only
skillctl list --project-only    # Show project skills only
skillctl inspect brainstorming  # Show details for a specific skill
skillctl stats                  # Show token statistics

# Manage the shim
skillctl shim install           # Install the shim
skillctl shim status            # Show shim status
skillctl shim check             # Verify CLI availability
skillctl shim remove            # Remove the shim

# Other
skillctl --version              # Show the version
```

### Advanced Options

```bash
# Limit skill scope
skillctl codex --global-only     # Load global skills only
skillctl codex --project-only    # Load project skills only

# Enable suggestion mode (show relevant skills without injecting them)
skillctl codex --suggest-skills

# Pass arguments through to the underlying CLI
skillctl codex --help
skillctl codex exec --json
```

### Escape the `@` Symbol

If you want to write the literal string `@brainstorming` without loading the skill:

```text
@@brainstorming is a skill name
```

## How It Works

```text
┌──────────┐     ┌─────────────┐     ┌──────────┐
│ User TTY │────→│  skillctl   │────→│ codex    │
│ (raw mode)│    │             │     │ (child PTY) │
│          │←────│ pass-through + injection │←────│          │
└──────────┘     └─────────────┘     └──────────┘
```

1. **Isolated runtime**: create a temporary HOME with an empty `skills/` directory and symlinks to the real auth files
2. **PTY proxy**: use `pty.fork()` to spawn the child process and put the real terminal into raw mode
3. **Transparent forwarding**: send keyboard input straight to the child process so the TUI keeps working
4. **Shadow tracking**: `ShadowBuffer` tracks what the user is typing in the background
5. **On-demand injection**: when Enter is pressed, parse `@skill_name`, inject the selected skill file, and then submit the message

### Skill Index

skillctl scans these directories for skills:

| Scope | Path |
|-------|------|
| global | `~/.codex/skills/`, `~/.claude/skills/`, `~/.gemini/skills/` |
| project | `./skills/`, `./.codex/skills/`, `./.claude/skills/`, `./.gemini/skills/` |

Each skill directory must contain one of `SKILL.md`, `CLAUDE.md`, `GEMINI.md`, or `README.md`.

Frontmatter can define the name, aliases, and description. `skillctl` uses those fields to build its local index and drive resolution or suggestions:

```markdown
---
name: brainstorming
description: creative ideation and feature planning
aliases: [brainstorm, ideate]
---

# Brainstorming

Help the user generate creative ideas...
```

### Priority

- Project scope wins when skill names collide
- Skills are isolated by CLI, so a `codex` session loads only `codex` and project skills

## Project Structure

```text
skillctl/
├── skillctl/
│   ├── __init__.py       # Version
│   ├── __main__.py       # Entry point
│   ├── cli.py            # CLI commands and argument parsing
│   ├── config.py         # Configuration and paths
│   ├── proxy.py          # PTY proxy, ShadowBuffer, LazySkillInjector
│   ├── registry.py       # Skill index building and cache
│   ├── resolver.py       # Skill resolution and suggestions
│   ├── runtime.py        # Runtime isolation
│   └── shims.py          # Shim installation and management
├── tests/                # Unit tests (49)
├── install.sh            # Install script
├── uninstall.sh          # Uninstall script
└── pyproject.toml        # Project configuration
```

## Environment Variables

| Variable | Description | Default |
|------|------|--------|
| `SKILLCTL_RUNTIME_ROOT` | Parent directory for temporary runtime directories | `$TMPDIR/skillctl-runtime` |
| `SKILLCTL_REAL_CODEX_BIN` | Path to the real codex binary (set automatically by the shim) | — |
| `SKILLCTL_REAL_CLAUDE_BIN` | Path to the real claude binary | — |
| `SKILLCTL_REAL_GEMINI_BIN` | Path to the real gemini binary | — |
| `PYTHON_BIN` | Python binary used by `install.sh` | `python3` |
| `SHIM_DIR` | Shim installation directory | `~/.local/bin` |

## Notes

- macOS and Linux are supported. Windows is not.
- `@skill_name` is the only trigger that loads a skill file into the session.
- Token statistics are rough estimates (`character_count / 4`). They reflect what `skillctl` injected, not the full internal accounting of the underlying CLI.
- `--suggest-skills` shows relevant local skills but does not inject them.
- For `--help`, `--version`, or explicit subcommands, skillctl passes the command straight through to the underlying CLI.
- The shim does not overwrite same-named files it does not manage.
- In long conversations, early injected skills may fall out of the context window.

## Development

```bash
# Run tests
python3 -m unittest discover -s tests -v

# Or use pytest
pip install -e ".[dev]"
pytest
```
