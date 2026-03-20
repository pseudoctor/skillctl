# skillctl

`skillctl` is a lazy-loading wrapper for the `claude`, `codex`, and `gemini` CLIs. Instead of loading every skill up front, it injects only the ones you actually call.

## What It Solves

Tools like Claude Code, Codex, and Gemini CLI support user-defined **skills**, usually stored in directories such as `~/.codex/skills/` and `~/.claude/skills/`.

**The problem**: most CLIs load every skill into the system prompt at startup. If you have 20 skills and each is about 2,000 tokens, that is **40,000 tokens** before you even start, whether you use them or not.

**skillctl's approach**:

```text
Native mode: CLI starts -> loads all 20 skills -> 40,000 tokens
skillctl:   CLI starts -> empty skills directory -> user requests @brainstorming -> injects only 1 -> ~2,000 tokens
                                                                                   savings ~= 95%
```

1. Create an isolated runtime with an empty skills directory while preserving auth and config
2. Build a lightweight skill index
3. Inject a skill only when the user types `@skill_name`
4. Report token savings when the session ends

## In Practice

Tested with `codex` using `@brainstorming help me`:

- ✅ The TUI still works, including arrow keys, Tab, and Ctrl+C
- ✅ Codex receives and understands the injected skill content
- ✅ Codex follows the brainstorming skill workflow as expected
- ✅ Token statistics are printed when the session ends

Savings depend on how many skills you have and how large they are:

| Scenario | Eager Load | skillctl | Savings |
|------|---------|----------|--------|
| 10 skills × 1000 tokens, 2 used | 10,000 | ~2,050 | ~80% |
| 20 skills × 2000 tokens, 1 used | 40,000 | ~2,050 | ~95% |
| 3 skills × 30 tokens, all used | 90 | ~120 | 0% (too small to matter) |

> **In short: this helps most when you have a lot of skills but only use a few in any given session.**

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

After installing the shim, you can keep using the CLI the same way you already do:

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

When you press Enter, skillctl injects the full skill content. After that, you usually do not need to mention it again because the skill is already in the conversation context.

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
5. **On-demand injection**: when Enter is pressed, parse `@skill_name`, inject the skill body, and then submit the message

### Skill Index

skillctl scans these directories for skills:

| Scope | Path |
|-------|------|
| global | `~/.codex/skills/`, `~/.claude/skills/`, `~/.gemini/skills/` |
| project | `./skills/`, `./.codex/skills/`, `./.claude/skills/`, `./.gemini/skills/` |

Each skill directory must contain one of `SKILL.md`, `CLAUDE.md`, `GEMINI.md`, or `README.md`.

Frontmatter can define the name, aliases, and description:

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

- Project scope wins over global scope when the skill names collide
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
- `@skill_name` is the only lazy-load trigger.
- Token statistics are rough estimates (`character_count / 4`). They are meant for comparison, not exact accounting.
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
