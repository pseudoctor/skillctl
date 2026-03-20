# skillctl

给 `claude`、`codex`、`gemini` CLI 用的懒加载 skill 启动器。通过按需注入替代全量加载，显著减少每次会话的 token 消耗。

## 解决什么问题

AI 编程 CLI（Claude Code、Codex、Gemini CLI）支持用户定义的 **skills**（自定义指令文件），存放在 `~/.codex/skills/`、`~/.claude/skills/` 等目录。

**问题**：CLI 启动时会把所有 skills 全量塞进系统提示词。如果你有 20 个 skills、每个约 2000 tokens，每次会话的系统提示词就包含 **40,000 tokens** — 即使你一个 skill 都没用到。

**skillctl 的方案**：

```
原生模式：   CLI 启动 → 加载全部 20 个 skills → 40,000 tokens
skillctl：  CLI 启动 → 空 skills 目录 → 用户请求 @brainstorming → 只注入 1 个 → ~2,000 tokens
                                                                    节省 ≈ 95%
```

1. 创建隔离运行时（空 skills 目录 + 保留认证和配置）
2. 建立轻量级 skill 索引
3. 只在用户输入 `@skill_name` 时注入对应 skill 正文
4. 会话结束时报告 token 节省情况

## 实际效果

使用 `codex` 实测，输入 `@brainstorming help me`：

- ✅ TUI 界面正常（方向键、Tab、Ctrl+C 均可用）
- ✅ codex 正确接收并理解注入的 skill 内容
- ✅ codex 按 brainstorming skill 的流程引导用户
- ✅ 会话结束时输出 token 统计

Token 节省效果取决于 skills 的数量和大小：

| 场景 | 全量加载 | skillctl | 节省率 |
|------|---------|----------|--------|
| 10 skills × 1000 tokens, 用 2 个 | 10,000 | ~2,050 | ~80% |
| 20 skills × 2000 tokens, 用 1 个 | 40,000 | ~2,050 | ~95% |
| 3 skills × 30 tokens, 全部使用 | 90 | ~120 | 0%（太小，不值得） |

> **结论：skills 越多、每次用到的越少，节省效果越显著。**

## 安装

### 前置要求

- macOS 或 Linux
- Python ≥ 3.10
- 至少安装了 `claude`、`codex`、`gemini` 中的一个

### 方式一：一行远程安装

```bash
curl -fsSL https://raw.githubusercontent.com/pseudoctor/skillctl/main/install.sh \
  | SKILLCTL_REPO_URL="https://github.com/pseudoctor/skillctl.git" sh
```

这条命令会自动：clone 仓库 → 验证可用 CLI → 安装 Python 包 → 重建 skill 索引 → 安装 shim。

### 方式二：本地安装

```bash
git clone https://github.com/pseudoctor/skillctl.git
cd skillctl
./install.sh
```

可选参数：

```bash
PYTHON_BIN=python3.12 SHIM_DIR="$HOME/.local/bin" ./install.sh
```

### 卸载

```bash
./uninstall.sh              # 保留缓存
REMOVE_CACHE=1 ./uninstall.sh  # 同时删除缓存
```

或远程卸载：

```bash
curl -fsSL https://raw.githubusercontent.com/pseudoctor/skillctl/main/uninstall.sh \
  | SKILLCTL_REPO_URL="https://github.com/pseudoctor/skillctl.git" sh
```

## 使用

### Shim 模式（推荐）

安装 shim 后，直接像以前一样使用 CLI：

```bash
# 安装 shim
skillctl shim install

# 直接使用，skillctl 会透明代理
codex
claude
gemini
```

shim 默认安装到 `~/.local/bin`。如不在 PATH 中，按提示添加：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

### 直接启动模式

不安装 shim 也可以用：

```bash
skillctl codex
skillctl claude
skillctl gemini

# 等价于
python3 -m skillctl codex
```

### 在会话中使用 @skill_name

启动 CLI 后正常使用，需要特定 skill 时加上 `@` 前缀：

```text
@brainstorming 帮我分析这个需求是否值得做
```

skillctl 会在你按下回车时注入该 skill 的完整内容。之后的消息无需再次引用 —— skill 已在对话上下文中。

### 常用命令

```bash
# 管理 skill 索引
skillctl index rebuild          # 重建索引

# 查看 skills
skillctl list                   # 列出所有 skill
skillctl list --global-only     # 只看全局 skill
skillctl list --project-only    # 只看项目 skill
skillctl inspect brainstorming  # 查看某个 skill 详情
skillctl stats                  # 查看 token 统计

# 管理 shim
skillctl shim install           # 安装 shim
skillctl shim status            # 查看 shim 状态
skillctl shim check             # 验证 CLI 可用性
skillctl shim remove            # 移除 shim

# 其他
skillctl --version              # 查看版本
```

### 高级选项

```bash
# 限制 skill 范围
skillctl codex --global-only     # 只加载全局 skill
skillctl codex --project-only    # 只加载项目 skill

# 启用建议模式（不自动加载，只提示相关 skill）
skillctl codex --suggest-skills

# 透传参数到底层 CLI
skillctl codex --help
skillctl codex exec --json
```

### 转义 @ 符号

如果你想在输入中写字面量 `@brainstorming` 而不触发加载：

```text
@@brainstorming 是一个 skill 名称
```

## 工作原理

```
┌──────────┐     ┌─────────────┐     ┌──────────┐
│ 用户终端  │────→│  skillctl   │────→│ codex    │
│ (raw mode)│     │             │     │ (子 PTY) │
│          │←────│ 透传 + 注入  │←────│          │
└──────────┘     └─────────────┘     └──────────┘
```

1. **隔离运行时**：创建临时 HOME 目录，内含空 `skills/` 和指向真实认证文件的 symlinks
2. **PTY 代理**：通过 `pty.fork()` 创建子进程，设置真实终端为 raw mode
3. **透明转发**：所有键盘输入立即转发给子进程（TUI 正常工作）
4. **影子追踪**：`ShadowBuffer` 在后台追踪用户打字内容
5. **按需注入**：检测到回车时，解析 `@skill_name`，将 skill 正文注入后再提交

### Skill 索引

skillctl 扫描以下目录查找 skills：

| scope | 路径 |
|-------|------|
| global | `~/.codex/skills/`, `~/.claude/skills/`, `~/.gemini/skills/` |
| project | `./skills/`, `./.codex/skills/`, `./.claude/skills/`, `./.gemini/skills/` |

每个 skill 目录需包含 `SKILL.md`、`CLAUDE.md`、`GEMINI.md` 或 `README.md` 中的一个。

支持 frontmatter 格式定义名称、别名和描述：

```markdown
---
name: brainstorming
description: creative ideation and feature planning
aliases: [brainstorm, ideate]
---

# Brainstorming

Help the user generate creative ideas...
```

### 优先级

- project scope 优先于 global scope（同名 skill）
- 各 CLI 的 skills 互相独立，`codex` 会话只加载 `codex` 和 `project` skills

## 项目结构

```
skillctl/
├── skillctl/
│   ├── __init__.py       # 版本
│   ├── __main__.py       # 入口
│   ├── cli.py            # CLI 命令和解析
│   ├── config.py         # 配置和路径
│   ├── proxy.py          # PTY 代理、ShadowBuffer、LazySkillInjector
│   ├── registry.py       # Skill 索引构建与缓存
│   ├── resolver.py       # Skill 解析和建议
│   ├── runtime.py        # 运行时隔离
│   └── shims.py          # Shim 安装/管理
├── tests/                # 单元测试（49 个）
├── install.sh            # 安装脚本
├── uninstall.sh          # 卸载脚本
└── pyproject.toml        # 项目配置
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `SKILLCTL_RUNTIME_ROOT` | 运行时临时目录的父目录 | `$TMPDIR/skillctl-runtime` |
| `SKILLCTL_REAL_CODEX_BIN` | 真实 codex 二进制路径（shim 自动设置） | — |
| `SKILLCTL_REAL_CLAUDE_BIN` | 真实 claude 二进制路径 | — |
| `SKILLCTL_REAL_GEMINI_BIN` | 真实 gemini 二进制路径 | — |
| `PYTHON_BIN` | install.sh 使用的 Python 路径 | `python3` |
| `SHIM_DIR` | shim 安装目录 | `~/.local/bin` |

## 注意事项

- 仅支持 macOS / Linux，不支持 Windows
- `@skill_name` 是唯一的懒加载触发方式
- Token 统计是粗略估算（`字符数 / 4`），用于对比全量和按需的量级差
- `--suggest-skills` 只在本地终端提示相关 skills，不自动注入
- 对 `--help` / `--version` / 显式子命令，skillctl 直接透传到底层 CLI
- Shim 不会覆盖非 skillctl 管理的同名文件
- 长对话中，较早注入的 skill 可能因 context window 截断而丢失

## 开发

```bash
# 运行测试
python3 -m unittest discover -s tests -v

# 或使用 pytest
pip install -e ".[dev]"
pytest
```
