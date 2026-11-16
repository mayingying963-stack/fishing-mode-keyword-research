# 环境要求与自检（Environment & Preflight）

这个 skill 的**关键词逻辑**不依赖任何特定产品、地区或工具，但**运行它**需要
满足一组环境条件。所有条件都在 `preflight()` 里被检查，在花钱之前就报出来，
而不是跑到一半才失败。

上手第一件事：

```bash
python scripts/fishing.py --check-env --mode api
# 或
python scripts/fishing.py --check-env --mode offline
```

输出示例：

```
python      : 3.11.15 (/usr/bin/python3)
platform    : linux
console enc : utf-8
mode        : api
requests    : ok (2.33.0)
openpyxl    : ok (3.1.5)
BLOCK : DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD are not set. ...
RESULT: not ready (see BLOCK lines)
```

`RESULT: ready` → 可以跑。`not ready` → 看 `BLOCK` 行，修完再跑。

---

## 1. 硬性要求（缺了就跑不了）

| 项 | 检查位置 | 不满足的后果 | 解决 |
|---|---|---|---|
| **Python ≥ 3.9** | `preflight()` | 代码用了 `dict \| dict` 和 `X \| Y` 注解，低版本语法报错 | 装 3.9+，推荐 3.10+ |
| **`requests`** | `preflight()`（仅 api 模式） | API 模式第一次调用就崩 | `pip install requests` |
| **`DATAFORSEO_LOGIN`** | `preflight()`（仅 api 模式） | 无法认证 | 见下方凭证说明 |
| **`DATAFORSEO_PASSWORD`** | `preflight()`（仅 api 模式） | 同上 | **API 专用密码**，不是账户登录密码 |
| **输出目录可写** | `main()` 中写入探针文件 | 会在跑完 150 个付费 SERP 后才发现写不进去 | 换可写目录 / 修权限 |

离线模式（`--mode offline`）只需要 Python 自身，不需要 `requests`，也不需要凭证。

## 2. 软性要求（缺了能跑，但产出降级）

| 项 | 后果 | 解决 |
|---|---|---|
| **`openpyxl`** | 所有 CSV 照常产出，只跳过 `.xlsx` 汇总工作簿 | `pip install openpyxl` |
| **控制台 UTF-8** | 中文日志可能乱码（**输出文件不受影响**，永远是 UTF-8+BOM） | 见第 4 节 |

## 3. 需要「连接」的外部服务

只有一样东西需要连：**DataForSEO REST API**。

```
BASE = https://api.dataforseo.com/v3/
认证 = HTTP Basic (login:password)
```

**不需要**：MCP 服务器、OpenSEO 网关、浏览器插件、SEO Minion。
（SEO Minion 只在你想用离线模式手动导出 SERP 时才涉及，且它产出的 Excel 直接喂给
`--mode offline` 即可，不需要安装任何东西到本 skill。）

凭证获取：https://app.dataforseo.com/api-access
- 页面上的 **API password** 才是 `DATAFORSEO_PASSWORD`
- 页面上给的 Base64 串就是 `login:password`，`echo <base64> | base64 -d` 可得

## 4. 已修复的跨机器问题

### 中文控制台崩溃（实测修复）

**问题**：在中文 Windows 默认控制台（cp936/cp1252）或 CI 的 ascii 环境里，
`print("以词找词")` 会抛 `UnicodeEncodeError`，整个流程在第一条日志就死掉。
这不是逻辑问题，纯粹是控制台编码。

**修复**：`_safe_stream()` 在 `main()` 最先执行，把 stdout/stderr 切成
`errors="backslashreplace"`，日志永远不会成为崩溃原因。

**实测**：`PYTHONIOENCODING` 设为 `utf-8 / ascii / cp1252` 三种情况，修复前
ascii 和 cp1252 均 `exit=1`，修复后全部 `exit=0`。

### 输出文件永远是 UTF-8 + BOM

所有 CSV 用 `encoding="utf-8-sig"` 写，**双击用 Excel 打开中文不乱码**，
在 zh-CN 机器上按 GBK 读也会失败而不是静默乱码（这点已实测）。

路径含中文和空格也验证通过（`输出 目录/sheet1.csv` 正常创建）。

## 5. 换地区/换市场要改什么

默认值面向美国市场：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--location` | `2840`（美国） | 换成目标市场的 DataForSEO location_code |
| `--language` | `en` | 语言代码 |

常见 location_code：

| 代码 | 市场 | 代码 | 市场 |
|---|---|---|---|
| 2840 | 美国 | 2826 | 英国 |
| 2124 | 加拿大 | 2036 | 澳大利亚 |
| 2276 | 德国 | 2250 | 法国 |
| 2392 | 日本 | 2152 | 新加坡 |

**这是唯一必须改的东西**——拉美国 SERP 去做德国市场，得到的竞品名单没法用。
完整列表见 `references/dataforseo-endpoints.md`。

## 6. 并发与速率

SERP 和域名挖掘都是 `max_workers=6` 并发，并对 `40101 Internal SE Server Error`
做 3 次退避重试。单任务失败只跳过该关键词/域名，不会中断整批。

如果你的 DataForSEO 套餐有并发限制，把代码里的 `max_workers=6` 调小即可
（`serp_top_domains` 和 `step3_domain_keywords` 各一处）。

## 7. 换产品品类要校准什么

逻辑是品类无关的，但三张人工维护的词表在极端品类上可能需要补充：

| 常量 | 作用 | 何时要加 |
|---|---|---|
| `ADJACENT_VERTICALS` | 跨行业异类名词（car/marine/rv/phone…） | 你的品类有特殊邻接词（如做船用电池要排除 `golf`） |
| `NON_PRODUCT_INTENT` | 服务类词（certified/rental/training/jobs…） | 你的行业有独特的服务词 |
| `ANTONYM_GROUPS` | 对立产品线（ac/dc、lead/lithium、on/off-grid） | 你的品类有成对的对立规格 |

**已知不能放进 `ADJACENT_VERTICALS` 的词**（放进去会误杀你自己的正常词）：

| 词 | 为什么不能放 |
|---|---|
| `truck` | 叉车本身就叫 **forklift truck**，放进去会剔掉 `battery for forklift truck`、`forklift truck battery chargers` |
| `home` / `residential` | 太阳能、储能产品的正常修饰词，会剔掉 `solar inverter for home` |
| `solar` | 只有当你的产品**不是**太阳能时才该当异类；做成硬编码会伤到太阳能品类 |

同理，`NON_PRODUCT_INTENT` 里不要放 `for sale`、`repair`——这些是正常商业词
（`forklift battery for sale`、`forklift battery repair` 都是有效关键词）。

判断方法：跑完先看 `step2_serp_dropped.csv`（每条都有 `reason`）和
`step3_domain_summary.csv`（每站 `offtopic_dropped` 比例）。

- 某类词被误杀 → 检查是否落在某张表里
- 某个同行站保留数=0 → 说明规则对该行业过严，优先用 `--context` 补充领域词
- 大量离题词漏进来 → 补 `ADJACENT_VERTICALS`

## 8. 三个站点的建议配置

| 站点 | 品类 | seeds 提示 |
|---|---|---|
| HIITIO | 高压直流器件 | 保证 5 个种子共享 `dc` / `contactor` / `fuse` 这类行业词 |
| LEAP | EV 电池包 | 注意 `EV` 与 `car/marine` 的边界，`--context` 补 `ev` |
| HIITIO Semi | 半导体模块 | `module` 是泛词，种子必须带 `igbt`/`sic`/`power` 区分 |
| BlitzNXT | 锂电池 | `golf cart` 是主营，种子里必须出现，否则会被当异类剔除 |
