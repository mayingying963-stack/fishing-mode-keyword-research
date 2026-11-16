# 写一个能被正确触发的 description

`description` 是 skill 被加载前**唯一**被读到的内容。占位符写得像文档，
就会在真正该用的时候不被想起；写得太宽，又会跟兄弟 skill 抢活。

这份笔记来自一次实际改写：初版顺着流程描述写成技术文档，用户一句
「给我写个 description」之后重写，触发词从 6 个扩到 16 个。

---

## 1. 先回答「是不是我」，而不是「我做什么」

初版（被否）：

```
Use when the user wants a full keyword universe for one product built from
scratch — the "捕鱼法 / Fishing-Mode" SOP (SEO小平). Five seed keywords →
keyword-ideas expansion → top-50 Google SERP per keyword → junk-domain
filtering → ...
```

问题：前半句还算触发，后半句全是**算法**。模型在决定「要不要加载」时不需要
知道步骤，只需要判断**当前请求属于哪一类任务**。

改写后（采用）：

```
Use when the user wants to build a keyword universe for ONE product from
scratch — 捕鱼法 / Fishing-Mode (SEO小平's SOP), not an audit of existing
rankings. ...
```

一句话就把三件事说完了：**任务形状**（为单个产品从零建词库）、
**方法名**（捕鱼法 / Fishing-Mode）、**边界**（不是审计）。

---

## 2. 用用户自己的说法，中英都要

最容易被忽略的一条。改写前，description 里有 `关键词词库`、`以词找词`，
但没有用户真实说过的：

| 用户原话 | 改写前 | 改写后 |
|---|---|---|
| 「帮我挖一批词」 | ❌ | ✅ `挖词` |
| 「选词」 | ❌ | ✅ `选词` |
| 「竞品关键词」 | ❌ | ✅ `竞品关键词` |
| 「同行域名」 | ❌ | ✅ `同行域名` |
| 「换个说法试试」 | — | 这就是要覆盖的东西 |

判断方法：把这轮对话里用户**实际打出来的字**过一遍，凡是描述「他要什么」的
短语，都应该进 description。凭空造的同义词不如用户的原话。

---

## 3. 必须写「我不是什么」

没有负面边界，模型无法在相似的 skill 之间做选择。

这个 skill 加了：

```
not an audit of existing rankings
```

因为「关键词调研」这句话既能指**从零建词库**（本 skill），也能指
**审计已有站点排名**（`seo-audit` / `seo-page`）。不写这一句，两者会互相抢。

兄弟 skill 里的同类写法：

| Skill | 反触发句 |
|---|---|
| 本 skill | not an audit of existing rankings |
| `seed-keyword-research` | 判断**单个**词是否值得建站（不是批量建库） |
| `seo-audit` | 全站审计，不是单页分析 |

---

## 4. 把「决定成本」所需的信息放进去

如果加载 skill 意味着可能花掉 $3–40，那这个信息应该在选择阶段就可见：

```
Runs on DataForSEO API (成本约 $1.6–3/次) or fully offline from SEO Minion /
Semrush / Ahrefs exports. Ships scripts/fishing.py, an xlsx exporter, and 194
offline assertions.
```

两个作用：

1. 用户看到「$1.6–3/次」就知道跑之前该看余额（这次会话里用户确实对
   $39.5 的选项感到意外，因为事先没人告诉他量级）。
2. 「194 offline assertions」让加载它的模型**立刻知道有可靠自检可跑**，
   不必先怀疑代码质量。

---

## 5. 长度预算

上限 1024 字符（validator 强制）。实测：

| 版本 | 长度 | 结果 |
|---|---|---|
| 初版 | 约 520 | 触发词不足 |
| 改写版 | **784** | 采用 |

经验区间 **700–900**。低于 500 通常是触发词不够；贴近 1024 会把正文内容
重复进来，反而稀释信号（description 是每轮都要付的上下文成本）。

---

## 6. 中文触发词不要加引号

```yaml
# 好
Triggers on 捕鱼法, 种子词, 以词找词, 挖词, keyword universe, SEO小平.

# 差
Triggers on "捕鱼法", "种子词", ...
```

YAML 折叠（`>-`）之后，引号会**留在文本里**，变成 `"捕鱼法"` 这样的字面量，
既占字符又干扰匹配。中文本身的辨识度足够，不需要包裹。

---

## 7. 校验脚本

改写后必须跑一次，确认 YAML 可解析且长度合规：

```python
import pathlib, re, yaml

c = pathlib.Path("SKILL.md").read_text(encoding="utf-8")
assert c.startswith("---"), "must start with ---"
m = re.search(r"\n---\s*\n", c[3:])
fm = yaml.safe_load(c[3:m.start() + 3])

desc = fm["description"]
assert len(desc) <= 1024, f"description too long: {len(desc)}"
assert "\n" not in desc, "YAML folding failed"
assert desc.lower().startswith("use when"), "convention: start with 'Use when'"

# 触发词覆盖
for t in ["捕鱼法", "种子词", "挖词", "keyword universe"]:
    assert t in desc, f"missing trigger: {t}"
# 反触发
assert "not an audit" in desc.lower()
```

注意：`description: >-` 是**折叠标量**，`yaml.safe_load` 之后换行会变成空格。
如果上面 `"\n" not in desc` 失败，说明 YAML 写坏了（比如某行缩进不对）。

---

## 8. 生效时机

description 在**新会话**加载 skill 索引时才读取。当前会话的索引是启动时缓存的，
所以改完当场看不到效果 — 这是正常的，不是没写进去。

验证方式：开一个新会话，用用户的原话试触发
（比如「帮我给 LEAP 挖一批词」），看是否命中本 skill 而不是 `seo-audit`。
