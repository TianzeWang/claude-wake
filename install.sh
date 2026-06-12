#!/usr/bin/env bash
# install.sh — 把 cwake 安装到 PATH，使其可在任意目录直接调用。
# 用法：  ./install.sh            （默认装到 ~/.local/bin）
#         BINDIR=/opt/homebrew/bin ./install.sh   （自定义安装目录）
set -euo pipefail

# ── 解析本脚本所在的仓库目录（即使经软链调用也能找对）──
SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
REPO="$(cd -P "$(dirname "$SOURCE")" && pwd)"

CWAKE="$REPO/cwake"
BINDIR="${BINDIR:-$HOME/.local/bin}"
LINK="$BINDIR/cwake"

echo "claude-wake 目录: $REPO"

# ① 基本检查
[[ -f "$CWAKE" ]]       || { echo "✗ 找不到 $CWAKE"; exit 1; }
[[ -f "$REPO/app.py" ]] || { echo "✗ 找不到 app.py，目录不对？"; exit 1; }
grep -q "CLAUDE_WAKE_CONFIG" "$REPO/app.py" \
  || echo "⚠ app.py 未含 env 覆盖支持，多 wd 并行可能失效（需更新 app.py）"

# ② 依赖检查（缺了只警告，不阻断）
for dep in python3 tmux curl; do
  command -v "$dep" >/dev/null 2>&1 || echo "⚠ 缺少依赖: $dep"
done

# ③ 赋可执行权限 + 软链到 PATH
chmod +x "$CWAKE"
mkdir -p "$BINDIR"
ln -sf "$CWAKE" "$LINK"
echo "✔ 已软链: $LINK -> $CWAKE"

# ④ 检查 BINDIR 是否在 PATH
case ":$PATH:" in
  *":$BINDIR:"*) echo "✔ $BINDIR 已在 PATH" ;;
  *) echo "⚠ $BINDIR 不在 PATH，请加入后再用："
     echo "    echo 'export PATH=\"$BINDIR:\$PATH\"' >> ~/.zshrc && source ~/.zshrc" ;;
esac

echo
echo "完成。新开终端（或执行 hash -r）后，在任意项目目录里敲：cwake"
