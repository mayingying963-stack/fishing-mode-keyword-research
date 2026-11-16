#!/usr/bin/env bash
# Push the fishing-mode skill to GitHub.
#
# Precondition: create an empty repo at https://github.com/new (no README,
# no .gitignore), then create a PAT at https://github.com/settings/tokens
# with the "repo" scope.
#
# Usage:
#   GH_TOKEN=ghp_xxx bash push_to_github.sh https://github.com/<you>/<repo>.git
#   GH_TOKEN=ghp_xxx bash push_to_github.sh <url> http://127.0.0.1:7890   # via proxy
#
# The token is read from the environment so it never lands in this file or in
# your shell history.
set -u

REPO_URL="${1:-}"
PROXY="${2:-}"

if [ -z "$REPO_URL" ]; then
  cat <<'USAGE'
用法:
  GH_TOKEN=<你的token> bash push_to_github.sh https://github.com/<用户名>/<仓库名>.git [代理]

准备:
  1. https://github.com/new  建空仓库（不要勾 README / gitignore / license）
  2. https://github.com/settings/tokens  生成 token，勾 repo 权限
USAGE
  exit 2
fi

if [ -z "${GH_TOKEN:-}" ]; then
  echo "错误: 未设置 GH_TOKEN 环境变量"
  echo "示例: GH_TOKEN=ghp_xxx bash push_to_github.sh \\"
  echo "        https://github.com/you/fishing-mode-keyword-research.git"
  exit 2
fi

SKILL_DIR="/d/Hermes/skills/keyword-research/fishing-mode-keyword-research"
cd "$SKILL_DIR" || { echo "找不到 skill 目录: $SKILL_DIR"; exit 1; }

echo "== 1/4 当前提交 =="
git log --oneline -3 || exit 1

echo "== 2/4 配置远端 =="
git remote remove origin 2>/dev/null
git remote add origin "$REPO_URL"

if [ -n "$PROXY" ]; then
  echo "   代理: $PROXY"
  export http_proxy="$PROXY" https_proxy="$PROXY" all_proxy="$PROXY"
  git config http.proxy "$PROXY"
  git config https.proxy "$PROXY"
else
  git config --unset http.proxy 2>/dev/null
  git config --unset https.proxy 2>/dev/null
fi

echo "== 3/4 推送 =="
# Credentials go through a per-command helper so nothing is written to config.
export GIT_ASKPASS=/bin/echo
export GIT_TERMINAL_PROMPT=0
PUSH_OK=0
git -c credential.helper="" -c "http.extraHeader=Authorization: Basic $(printf 'x-access-token:%s' "$GH_TOKEN" | base64 -w0)" \
    push origin HEAD:main --force && PUSH_OK=1

if [ "$PUSH_OK" = "1" ]; then
  echo "== 4/4 完成 =="
  git branch -M main 2>/dev/null
  echo "仓库地址: $REPO_URL"
else
  echo
  echo "推送失败。常见原因："
  echo "  1. github.com 连不上 —— 本机实测 https://github.com 直连超时、连接被重置。"
  echo "     加代理重试:  GH_TOKEN=... bash push_to_github.sh <url> http://127.0.0.1:7890"
  echo "  2. token 权限不足或过期 —— 重新生成，确保勾选 repo。"
  echo "  3. 远端仓库已有冲突内容 —— 脚本已带 --force，仍失败则先清空远端。"
  exit 1
fi
