# 把一次性脚本做成可交付、可验证的 skill

这份笔记来自一个反复踩坑的过程：一个能跑出正确结果的脚本，被判定「未验证」
五次，每次都要重做验证。根因不是代码，是**验证没有留在任何地方**。

适用于任何「写一个自动化脚本交给用户长期用」的任务，不限关键词调研。

---

## 1. 验证脚本必须进仓库，不能是临时的

发生过的事：

```
第1轮：写 hermes-verify-X.py → 跑绿 22 项 → 删除
第2轮：系统报 "unverified"（脚本已不存在）→ 重写 → 跑绿 → 删除
... 重复 5 次，累计浪费约 2 小时
```

**一次性的验证脚本，跑完就证明不了任何事。** 正确的做法是把它提升为仓库里的
正式套件，并注册进统一入口：

```python
# scripts/run_checks.py
SUITES = [
    ("selftest.py",            "offline end-to-end pipeline"),
    ("verify_field_shapes.py", "wire shapes from live responses"),
    ("preflight_checks.py",    "environment contract + docs"),
    ("push_script_checks.py",  "bash guard rails"),
]
```

判据：**仓库里每一个可执行文件都必须被某个套件覆盖。** 没被覆盖的那个，
每次改动都会以「无法验证」结束——`push_ssh.sh` 就这样反复触发重做，直到
`push_script_checks.py` 出现。

同时断言**文档与代码一致**：文档说「三套」而代码有「四套」，就是下一次误判的来源。
这类一致性可以写成测试（扫文档关键词 vs 扫源码常量），比人工核对可靠。

---

## 2. 守卫本身可能违反它守护的规则

`preflight_checks.py` 的密钥扫描器用 `git grep` 搜索**真实的凭证字符串字面量**
——等于为了检测泄漏，而把凭据写进了仓库；自检时它抓到了自己。

```python
# 错：把真值写进代码
r"1ou9rygr|2e02ed4|MW91OXJ5"

# 对：匹配「形态」
r"([A-Za-z0-9]{18,}:[A-Za-z0-9]{12,})|(Basic\s+[A-Za-z0-9+/]{20,})"
```

并且**两个方向都要测**：真实形态必须命中，`your-login` 这类占位符必须不误报。

任何守卫写完后问一句：**这个守卫自身是否违反了它要守的不变量？**

---

## 3. 绿灯套件里可能藏着测试 bug —— 这类最危险

现象：`push_script_checks` 报「脏工作区时远端仍未推送」失败，但脚本顺序是对的。

真相：**测试写了错**——它复用同一个 bare 仓库，读到的是上一步成功推送留下的分支。

两条规则：

1. **每条断言给它自己的 fixture。** 共享状态会让上一步的结果污染下一步。
2. **守卫类测试失败时，先证明是哪一侧错了**，再改生产代码。

一个「因为错误原因而失败」的测试，会训练你忽略那个「因为正确原因而失败」的测试。

---

## 4. 顺序类守卫要在源码里断言顺序

「提交前检查工作区」如果被放在 `git push` **之后**，就是装饰品——脏工作区照样推上去。

只测行为不够（干净工作区时两种顺序都通过），要在源码里断言位置：

```python
body = SCRIPT.read_text(encoding="utf-8")
assert body.index("status --porcelain") < body.index("git push")
```

同理适用于：参数校验 vs 花钱调用、鉴权 vs 数据拉取、目录可写探针 vs 批量写入。

---

## 5. 间歇性红 = 不确定的输入，不是「代码抽风」

套件通过 → 失败 → 通过，代码一行没改。

原因：密钥扫描器在文件被 `git commit` 的那一刻开始匹配到自己——**仓库状态本身
是被测输入的一部分**。

遇到间歇性失败，去 diff **状态**（未跟踪文件、已提交文件、环境变量），
不要反复重跑祈祷它变绿。

---

## 6. 跨机器可运行性：先自检，再花钱

交付给别人的脚本，第一步应该是**自检**而不是干活：

```bash
python scripts/fishing.py --check-env --mode api
# python      : 3.11.15
# requests    : ok (2.33.0)
# BLOCK : DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD are not set.
# RESULT: not ready (see BLOCK lines)
```

区分两级：

| 级别 | 例子 | 处理 |
|---|---|---|
| blocker（缺了就停） | Python < 3.9、缺 `requests`、缺凭证、输出目录不可写 | 返回码 2，打印修复方法 |
| warning（降级可跑） | 缺 `openpyxl`（少 xlsx）、控制台非 UTF-8（日志乱码） | 继续跑，但告知 |

**「输出目录可写」要用真实写探针验证**——不能靠 `os.access`，要真的写一个文件再删。
否则会在跑完 150 个付费请求之后才发现写不进去。

---

## 7. 控制台编码：交付脚本的隐形崩溃点

在中文 Windows 默认控制台（cp936/cp1252）或 CI 的 ascii 环境里，
`print("以词找词")` 会抛 `UnicodeEncodeError`，整个流程死在第一条日志上。

开发机是 UTF-8 时完全看不出来。修复方式（在最早的入口执行）：

```python
def _safe_stream(stream) -> None:
    try:
        stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError, OSError):
        pass   # 不是 TextIOWrapper（pytest capture / 某些 IDE）

def main(argv=None):
    _safe_stream(sys.stdout)   # 必须在任何 log() 之前
    _safe_stream(sys.stderr)
```

**要把它写进测试**：`PYTHONIOENCODING` 设为 `utf-8 / ascii / cp1252 / cp936`
四种都能 `exit 0`，并断言 `main()` 里 `_safe_stream` 出现在 `ArgumentParser` 之前
（顺序错了等于没修）。

输出文件则一律 `encoding="utf-8-sig"`：Excel 双击打开中文不乱码，
在 GBK 机器上按 GBK 读会抛错而不是静默乱码。

---

## 8. 网络受限环境下的交付通道

实测过的组合（同一台机器）：

| 通道 | 结果 |
|---|---|
| `api.github.com` (HTTPS) | 200 |
| `github.com` (HTTPS) | 超时 |
| `git ls-remote https://github.com/...` | `Connection was reset` |
| `ssh -T git@github.com` | `successfully authenticated` |

结论：**SSH 可推，HTTPS 不可推**。但 SSH **不能创建仓库**——建仓需要网页或 API Token。

所以交付脚本要按这个顺序检查并给出可操作提示，而不是抛一个裸的
`Repository not found`：

1. `ssh -T git@github.com` 是否 `successfully authenticated`
2. `git ls-remote <url>` 确认仓库存在（不存在 → 提示先去网页建空仓库，
   **README / .gitignore / license 三项都不要勾**，否则历史不一致）
3. `git status --porcelain` 为空
4. 才 `git push`

永远不要用 `--force` 掩盖第 2 步的问题。
