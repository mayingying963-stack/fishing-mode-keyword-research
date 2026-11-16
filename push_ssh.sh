#!/usr/bin/env bash
# Push the fishing-mode skill over SSH (no token needed).
#
# The repo must already exist on GitHub — SSH cannot create it. Create an EMPTY
# repo at https://github.com/new (no README, no .gitignore, no license).
#
# Usage:
#   bash push_ssh.sh                                  # uses the default below
#   bash push_ssh.sh git@github.com:<user>/<repo>.git # override
set -u

DEFAULT_URL="git@github.com:mayingying963-stack/fishing-mode-keyword-research.git"
REPO_URL="${1:-$DEFAULT_URL}"
SKILL_DIR="/d/Hermes/skills/keyword-research/fishing-mode-keyword-research"

cd "$SKILL_DIR" || { echo "找不到 skill 目录: $SKILL_DIR"; exit 1; }

echo "== 1/5 检查 SSH 认证 =="
if ! ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -T git@github.com 2>&1 | grep -q "successfully authenticated"; then
  echo "SSH 认证失败。检查 ~/.ssh/id_ed25519 与 GitHub 上的公钥配置。"
  exit 1
fi
echo "   SSH OK"

echo "== 2/5 检查远端仓库是否存在 =="
if ! timeout 40 git ls-remote "$REPO_URL" >/dev/null 2>&1; then
  echo "   仓库不存在或无权访问: $REPO_URL"
  echo "   请先在 https://github.com/new 建一个空仓库（不要勾 README/gitignore/license）"
  exit 1
fi
echo "   仓库可访问"

echo "== 3/5 确认工作区干净 =="
if [ -n "$(git status --porcelain)" ]; then
  echo "   有未提交改动，先提交："
  git status --short
  exit 1
fi
git log --oneline -3

echo "== 4/5 推送 =="
git remote remove origin 2>/dev/null
git remote add origin "$REPO_URL"
git branch -M main
if timeout 120 git push -u origin main; then
  echo "== 5/5 完成 =="
  echo "仓库地址: ${REPO_URL/git@github.com:/https:\/\/github.com\/}"
  echo "          ${REPO_URL%.git}"
else
  echo "推送失败。检查 SSH key 是否有该仓库的写权限。"
  exit 1
fi
