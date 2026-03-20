# skillctl

`skillctl` 是一个给 `claude`、`codex`、`gemini` CLI 用的外层懒加载启动器。

它解决的问题很直接：
- 启动时不默认把所有 global/project skills 全量塞进上下文。
- 先建立一个轻量 skill 索引。
- 只有当你在输入里显式写 `@skill_name` 时，才注入该 skill 正文。

## 当前能力

- 扫描全局和项目 skill
- 生成本地索引缓存
- `@skill_name` 按需加载
- 本地粗略 token 统计
- 项目同名 skill 覆盖全局 skill
- 为 `claude`、`codex`、`gemini` 构建隔离运行时
- 安装 shim 后继续直接使用 `claude`、`codex`、`gemini`

## 快速开始

### GitHub 一行安装

当前仓库地址：
- `https://github.com/pseudoctor/skillctl`

下面示例默认使用 `main` 分支。如果你的默认分支不是 `main`，把命令里的 `main` 替换成真实分支名。当前仅支持 macOS / Linux。

macOS / Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/pseudoctor/skillctl/main/install.sh | SKILLCTL_REPO_URL="https://github.com/pseudoctor/skillctl.git" SKILLCTL_REPO_REF="main" sh
```

这条命令会：
- 下载安装脚本
- 先验证 `claude` / `codex` / `gemini` 真实 CLI 二进制都能找到
- 自动 clone 仓库到本地默认目录
- 执行安装、索引重建和 shim 安装

### GitHub 一行卸载

macOS / Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/pseudoctor/skillctl/main/uninstall.sh | SKILLCTL_REPO_URL="https://github.com/pseudoctor/skillctl.git" SKILLCTL_REPO_REF="main" sh
```

如果还要一并删除缓存：

macOS / Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/pseudoctor/skillctl/main/uninstall.sh | REMOVE_CACHE=1 SKILLCTL_REPO_URL="https://github.com/pseudoctor/skillctl.git" SKILLCTL_REPO_REF="main" sh
```

这条命令会：
- 下载卸载脚本
- 定位本地安装目录，必要时临时 clone 仓库
- 移除 shim
- 卸载 Python 包
- 可选删除安装仓库里的 `.skillctl` 缓存

### 一键安装

macOS / Linux:

```bash
cd /Users/armewang/Documents/CS-Tech/Local/skillctl
chmod +x install.sh uninstall.sh
./install.sh
```

如果你想指定 Python 或 shim 目录：

```bash
PYTHON_BIN=python3 SHIM_DIR="$HOME/.local/bin" ./install.sh
```

### 一键卸载

macOS / Linux:

```bash
cd /Users/armewang/Documents/CS-Tech/Local/skillctl
./uninstall.sh
```

如果你还想一并删除本地缓存：

```bash
REMOVE_CACHE=1 ./uninstall.sh
```

### 1. 重建索引

```bash
python3 -m skillctl index rebuild
```

### 2. 查看可见 skill

```bash
python3 -m skillctl list
python3 -m skillctl inspect brainstorming
python3 -m skillctl stats
```

如果你只想看全局或项目 skill：

```bash
python3 -m skillctl list --global-only
python3 -m skillctl list --project-only
python3 -m skillctl stats --global-only
python3 -m skillctl inspect brainstorming --project-only
```

### 3. 直接启动包装器

```bash
python3 -m skillctl codex
python3 -m skillctl claude
python3 -m skillctl gemini
```

如果你只想在当前会话里暴露某一层：

```bash
python3 -m skillctl codex --global-only
python3 -m skillctl claude --project-only
```

如果你想启用“候选 skill 提示但不自动加载”：

```bash
python3 -m skillctl codex --suggest-skills
python3 -m skillctl claude --suggest-skills
python3 -m skillctl gemini --suggest-skills
```

在会话里显式请求 skill：

```text
@brainstorming 帮我分析这个需求是否值得做
```

## 安装 shim

如果你不想每次都敲 `python3 -m skillctl ...`，可以安装 shim：

```bash
python3 -m skillctl shim install
python3 -m skillctl shim status
```

默认安装到：

```text
~/.local/bin
```

如果该目录不在 `PATH`，工具会提示你加入：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

安装后，你可以继续直接敲：

```bash
claude
codex
gemini
```

这些命令会先进入 `skillctl`，再由它转发到底层 CLI。

如果你想把 `--help`、`--version` 或显式子命令直接透传到底层 CLI，也可以直接这样用：

```bash
python3 -m skillctl codex --help
python3 -m skillctl codex exec ...
```

移除 shim：

```bash
python3 -m skillctl shim remove
```

如果你更习惯脚本方式，直接使用：

```bash
./install.sh
./uninstall.sh
```

## 注意

- 当前版本的交互代理仍然是 MVP，但输入处理已经按“提交边界”工作，普通回车提交、多行粘贴和分片输入会比初版稳定很多。
- `@skill_name` 是唯一正式支持的懒加载触发方式。
- 当前仅支持 macOS / Linux。
- `--suggest-skills` 会把可能相关的 skills 提示到本地终端，但不会自动注入 skill 正文。
- 对 `--help` / `--version` / 显式子命令，`skillctl` 会直接透传到底层 CLI，而不是进入懒加载交互代理。
- `--global-only` 和 `--project-only` 可以限制当前命令只看到某一层 skill。
- 如果你只想输入字面量 `@brainstorming` 而不触发加载，可以写成 `@@brainstorming`。
- token 统计是粗略估算，用于比较“全量 skill 载入”和“按需注入”的量级差，不是模型厂商的精确计费值。
- `REMOVE_CACHE=1` 目前只删除安装仓库目录中的 `.skillctl` 缓存，不会扫描并删除其他项目工作区里的 `.skillctl`。
- 还没有做关键词自动匹配和更细的 token 统计。
