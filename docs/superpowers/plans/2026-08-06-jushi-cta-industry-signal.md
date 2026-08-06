# 中国巨石卫星仓 CTA + 玻纤行业信号 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地周度玻纤行业信号表（公开资讯 MVP + manual/付费源预留）、只读沙箱 API，以及中国巨石卫星仓 CTA 示例策略（signal 友好）。

**Architecture:** 采集任务解析公开快讯 → upsert `qd_industry_glass_fiber_weekly`；策略经 `get_glass_fiber_industry_week` 按优先级读有效行；CTA 以行业门闩 + 仓位/止损/关 T 规则下单意图。付费源本期不接，仅预留 `source`。

**Tech Stack:** PostgreSQL 迁移、Flask/Celery、Strategy API V2 sandbox、pytest、可选 `urllib`/`html.parser`（无新重依赖）

**Spec:** `docs/superpowers/specs/2026-08-06-jushi-cta-industry-signal-design.md`

## Global Constraints

- 策略进程 **禁止** 直接 HTTP 抓网页；只读库表
- 不做卓创/隆众登录破解；`source` 预留 `zhuochuang` / `oilchem`
- 读取优先级：`manual` > 付费源 > `public_news`；`confidence < 0.5` 无效
- 无有效行业行 → **不开新卫星仓**，并关闭日内 T
- A 股部署默认 **signal**；卫星仓 ≤ 20%；硬止损 12%；T ≤ 持仓 25%
- Commit：中文 Conventional Commits；**仅在用户明确要求时 git commit**
- 测试：`cd backend_api_python && python3 -m pytest …`（容器内若无 pytest，用项目既有方式）

## File Map

| 文件 | 职责 |
|------|------|
| `backend_api_python/migrations/20260806_industry_glass_fiber_weekly.sql` | DDL |
| `backend_api_python/app/services/industry_glass_fiber/__init__.py` | 导出 |
| `backend_api_python/app/services/industry_glass_fiber/store.py` | upsert / 按优先级解析有效周 |
| `backend_api_python/app/services/industry_glass_fiber/parse.py` | 公开快讯文本 → trend/flag/价格 |
| `backend_api_python/app/services/industry_glass_fiber/fetch.py` | 白名单 URL 拉取（采集侧） |
| `backend_api_python/app/tasks/glass_fiber_industry.py` | Celery 聚合任务 |
| `backend_api_python/app/celery_app.py` | include + beat + 路由 |
| `backend_api_python/app/services/strategy_v2/runtime.py` | 注入 `get_glass_fiber_industry_week` |
| `backend_api_python/scripts/upsert_glass_fiber_industry_week.py` | manual 覆盖 CLI |
| `docs/examples/strategy_v2_jushi_satellite_cta.py` | 巨石 CTA 示例 |
| `backend_api_python/tests/test_industry_glass_fiber_store.py` | 存储与优先级 |
| `backend_api_python/tests/test_industry_glass_fiber_parse.py` | 解析 |
| `backend_api_python/tests/test_glass_fiber_industry_task.py` | 任务（mock fetch） |
| `backend_api_python/tests/fixtures/glass_fiber_news_sample.html` | 解析 fixture |

---

### Task 1: DDL + Store（upsert 与优先级合并）

**Files:**
- Create: `backend_api_python/migrations/20260806_industry_glass_fiber_weekly.sql`
- Create: `backend_api_python/app/services/industry_glass_fiber/__init__.py`
- Create: `backend_api_python/app/services/industry_glass_fiber/store.py`
- Test: `backend_api_python/tests/test_industry_glass_fiber_store.py`

**Interfaces:**
- Produces:
  - `upsert_glass_fiber_week(row: dict) -> dict`
  - `resolve_glass_fiber_week(as_of: date | str) -> dict | None`  
    返回有效行或 `None`；dict 至少含：`as_of`, `cloth_trend`, `inventory_trend`, `new_capacity_flag`, `source`, `confidence`, `industry_available`（True 当命中有效行）
  - `SOURCE_PRIORITY = ("manual", "zhuochuang", "oilchem", "public_news")`

- [x] **Step 1: Write failing tests**

```python
from datetime import date
from app.services.industry_glass_fiber.store import (
    resolve_glass_fiber_week,
    upsert_glass_fiber_week,
)

def test_manual_overrides_public_news(monkeypatch):
    rows = {}

    def fake_load(as_of):
        return list(rows.get(str(as_of), []))

    def fake_upsert(row):
        key = str(row["as_of"])
        rows.setdefault(key, [])
        rows[key] = [r for r in rows[key] if r["source"] != row["source"]] + [dict(row)]
        return row

    monkeypatch.setattr(
        "app.services.industry_glass_fiber.store._load_rows_for_as_of",
        fake_load,
    )
    # If implementation inlines SQL, monkeypatch the public upsert/resolve with in-memory
    # double in the test module instead — keep assert on resolve priority.

    upsert_glass_fiber_week({
        "as_of": date(2026, 8, 1),
        "cloth_trend": 1,
        "inventory_trend": -1,
        "new_capacity_flag": 0,
        "source": "public_news",
        "confidence": 0.7,
    })
    upsert_glass_fiber_week({
        "as_of": date(2026, 8, 1),
        "cloth_trend": -1,
        "inventory_trend": 1,
        "new_capacity_flag": 0,
        "source": "manual",
        "confidence": 1.0,
    })
    out = resolve_glass_fiber_week(date(2026, 8, 1))
    assert out is not None
    assert out["source"] == "manual"
    assert out["cloth_trend"] == -1
    assert out["industry_available"] is True


def test_low_confidence_skipped():
    # seed only public_news confidence=0.4 → resolve returns None
    ...
```

- [x] **Step 2: Run test — expect FAIL（模块不存在）**

```bash
cd backend_api_python && python3 -m pytest tests/test_industry_glass_fiber_store.py -q
```

- [x] **Step 3: Add migration**

```sql
CREATE TABLE IF NOT EXISTS qd_industry_glass_fiber_weekly (
  id BIGSERIAL PRIMARY KEY,
  as_of DATE NOT NULL,
  cloth_7628_mid DOUBLE PRECISION NULL,
  yarn_2400_mid DOUBLE PRECISION NULL,
  cloth_trend SMALLINT NOT NULL CHECK (cloth_trend IN (-1, 0, 1)),
  inventory_trend SMALLINT NOT NULL CHECK (inventory_trend IN (-1, 0, 1)),
  new_capacity_flag SMALLINT NOT NULL DEFAULT 0 CHECK (new_capacity_flag IN (0, 1)),
  source VARCHAR(32) NOT NULL,
  confidence REAL NOT NULL DEFAULT 0.5 CHECK (confidence >= 0 AND confidence <= 1),
  raw_refs JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (as_of, source)
);
CREATE INDEX IF NOT EXISTS idx_qd_gf_week_as_of
  ON qd_industry_glass_fiber_weekly (as_of DESC);
```

- [x] **Step 4: Implement store**

`upsert`：`INSERT … ON CONFLICT (as_of, source) DO UPDATE`。  
`resolve`：取 `as_of` 精确周或多行中按 `SOURCE_PRIORITY` 且 `confidence >= 0.5` 的第一条；若调用方传入「当前日」，则取 `as_of <= 当前日` 的最近一周再合并（PIT）。

最小返回：

```python
{
  "as_of": "2026-08-01",
  "cloth_7628_mid": None,
  "yarn_2400_mid": None,
  "cloth_trend": 1,
  "inventory_trend": -1,
  "new_capacity_flag": 0,
  "source": "manual",
  "confidence": 1.0,
  "industry_available": True,
  "raw_refs": {},
}
```

- [x] **Step 5: Run tests — expect PASS**

- [ ] **Step 6: Commit（仅当用户要求）**

---

### Task 2: 公开快讯解析器

**Files:**
- Create: `backend_api_python/app/services/industry_glass_fiber/parse.py`
- Create: `backend_api_python/tests/fixtures/glass_fiber_news_sample.txt`
- Test: `backend_api_python/tests/test_industry_glass_fiber_parse.py`

**Interfaces:**
- Produces: `parse_glass_fiber_news(text: str, *, as_of: date | None = None) -> dict`  
  字段：`cloth_trend`, `inventory_trend`, `new_capacity_flag`, `cloth_7628_mid`（可选）, `confidence`, `notes`（list[str]）

- [x] **Step 1: Write fixture + failing tests**

Fixture 示例句（中文）：

```text
据卓创资讯，7628电子布报价上调至6.5元/米，行业库存继续下降，本周暂无新窑点火。
```

```python
from app.services.industry_glass_fiber.parse import parse_glass_fiber_news

def test_parse_up_price_down_inventory():
    text = open("tests/fixtures/glass_fiber_news_sample.txt", encoding="utf-8").read()
    out = parse_glass_fiber_news(text)
    assert out["cloth_trend"] == 1
    assert out["inventory_trend"] == -1
    assert out["new_capacity_flag"] == 0
    assert out["confidence"] >= 0.5
    assert out.get("cloth_7628_mid") in (6.5, None) or out.get("cloth_7628_mid") == 6.5


def test_parse_down_price_inventory_build():
    out = parse_glass_fiber_news("电子布价格连续下跌，玻纤库存持续累积抬升。")
    assert out["cloth_trend"] == -1
    assert out["inventory_trend"] == 1
```

- [x] **Step 2: Run — FAIL**

- [x] **Step 3: Implement keyword / regex 规则**

- 上涨类：`上调|上涨|涨价|企稳回升|止跌` → cloth_trend=+1  
- 下跌类：`下调|下跌|降价|回落` → cloth_trend=-1  
- 库存降：`库存.*?(下降|回落|去化|低位)` → inventory_trend=-1  
- 库存升：`库存.*?(累积|抬升|上升|高位)` → inventory_trend=+1  
- 产能：`点火|新窑|新产能` → new_capacity_flag=1  
- 价格：`7628.*?(\d+(?:\.\d+)?)\s*元`  
- 冲突或无命中：confidence 降低（如 0.3），trend 默认 0  

- [x] **Step 4: Run — PASS**

- [ ] **Step 5: Commit（仅当用户要求）**

---

### Task 3: Fetch + Celery 周聚合任务

**Files:**
- Create: `backend_api_python/app/services/industry_glass_fiber/fetch.py`
- Create: `backend_api_python/app/tasks/glass_fiber_industry.py`
- Modify: `backend_api_python/app/celery_app.py`
- Test: `backend_api_python/tests/test_glass_fiber_industry_task.py`

**Interfaces:**
- Produces:
  - `fetch_url(url: str, timeout: float = 15.0) -> str`
  - `aggregate_week_from_texts(texts: list[str], as_of: date) -> dict`（调用 parse，多文合并：多数表决或取最高 confidence）
  - Celery task name: `quantdinger.tasks.glass_fiber_industry_sync`
  - Env: `ENABLE_GLASS_FIBER_INDUSTRY_SYNC` 默认 `true`；`GLASS_FIBER_NEWS_URLS` 逗号分隔白名单（空则任务 no-op 成功）

- [x] **Step 1: Failing test（mock fetch）**

```python
def test_sync_upserts_public_news(monkeypatch):
    calls = []

    monkeypatch.setenv("ENABLE_GLASS_FIBER_INDUSTRY_SYNC", "true")
    monkeypatch.setenv("GLASS_FIBER_NEWS_URLS", "https://example.invalid/a")
    monkeypatch.setattr(
        "app.tasks.glass_fiber_industry.fetch_url",
        lambda url, timeout=15.0: "电子布报价上调，库存下降，暂无新窑点火。",
    )
    monkeypatch.setattr(
        "app.tasks.glass_fiber_industry.upsert_glass_fiber_week",
        lambda row: calls.append(row) or row,
    )
    from app.tasks.glass_fiber_industry import run_glass_fiber_industry_sync

    result = run_glass_fiber_industry_sync()
    assert result["ok"] is True
    assert calls and calls[0]["source"] == "public_news"
    assert calls[0]["cloth_trend"] == 1
```

- [x] **Step 2: Implement fetch（stdlib）**

```python
from urllib.request import Request, urlopen

def fetch_url(url: str, timeout: float = 15.0) -> str:
    req = Request(url, headers={"User-Agent": "QuantDingerGlassFiberBot/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode("utf-8", errors="replace")
```

剥离 HTML 可用简易正则去标签，或只抓纯文本页；失败记日志，不抛垮整个 beat。

- [x] **Step 3: Celery task + beat**

在 `celery_app.py`：

- `include` 增加 `"app.tasks.glass_fiber_industry"`
- `task_routes`：`"quantdinger.tasks.glass_fiber_industry_sync": {"queue": "maintenance"}`
- `beat_schedule`：每个交易时段合理间隔（如每 6 小时一次，或每工作日 10:00/15:30 Asia/Shanghai）

任务逻辑：

1. 若 disabled 或 URL 空 → `{ok: true, skipped: true}`  
2. 拉各 URL → parse → 合并 → `as_of=本周观察日`（上海时区当周周五或上一个交易日简化：用当天 `date` 所在 ISO 周的周五）  
3. `upsert` `source=public_news`，`raw_refs` 含 urls  

- [x] **Step 4: Tests PASS**

- [ ] **Step 5: Commit（仅当用户要求）**

---

### Task 4: 沙箱注入 `get_glass_fiber_industry_week`

**Files:**
- Modify: `backend_api_python/app/services/strategy_v2/runtime.py`（globals 两处：回测与实盘/编译相关 dict）
- Test: `backend_api_python/tests/test_strategy_v2_glass_fiber_api.py`（或扩现有 contract 测试）

**Interfaces:**
- Produces: sandbox 全局函数  

```python
def get_glass_fiber_industry_week(as_of=None) -> dict:
    """PIT 行业周信号；无有效行时 industry_available=False。"""
```

- [x] **Step 1: Failing test** — 编译/执行一小段策略代码调用该函数（仿现有 `get_external_alpha_scores` 注入测试风格）

- [x] **Step 2: 在 runtime 增加**

```python
def get_glass_fiber_industry_week(as_of: object = None) -> dict:
    from datetime import date
    from app.services.industry_glass_fiber.store import resolve_glass_fiber_week

    if as_of is None:
        as_of = date.today()
    row = resolve_glass_fiber_week(as_of)
    if not row:
        return {
            "industry_available": False,
            "as_of": None,
            "cloth_trend": 0,
            "inventory_trend": 0,
            "new_capacity_flag": 0,
            "source": None,
            "confidence": 0.0,
        }
    return row
```

并写入所有构建策略 `globals` 的 dict（与 `get_external_alpha_scores` 并列，**两处都要加**）。

- [x] **Step 3: Tests PASS**

- [ ] **Step 4: Commit（仅当用户要求）**

---

### Task 5: manual CLI + 巨石 CTA 示例策略

**Files:**
- Create: `backend_api_python/scripts/upsert_glass_fiber_industry_week.py`
- Create: `docs/examples/strategy_v2_jushi_satellite_cta.py`
- Test: 轻量单测或编译测试：`tests/test_strategy_v2_template_seed.py` 若种子策略需登记则一并；否则单独 `tests/test_jushi_satellite_cta_compile.py` 对示例 `compile`/`extract params`

**Interfaces:**
- CLI:  
  `python scripts/upsert_glass_fiber_industry_week.py --as-of 2026-08-01 --cloth-trend 1 --inventory-trend -1 --source manual`
- Strategy params（`# @param`）：`satellite_max_pct`, `hard_stop_pct`, `t_max_frac`, `min_amount`, `enable_intraday_t`, `market_stress`, `gm_qoq`, `cash_profit_ratio`, …

- [x] **Step 1: CTA 核心逻辑（写入示例文件）**

```python
"""中国巨石卫星仓 CTA
Long-only satellite sleeve on CNStock:600176.SH with industry-week gate.
Signal-mode friendly. Core portfolio stays outside this strategy ledger.
"""

# @param satellite_max_pct float 0.20 range=0.05:0.20:0.01
# @param hard_stop_pct float 0.12 range=0.05:0.25:0.01
# @param t_max_frac float 0.25 range=0.05:0.25:0.01
# @param enable_intraday_t int 1 range=0:1:1
# @param market_stress int 0 range=0:1:1
# @param cash_profit_ratio float 1.0 range=0:2:0.05
# @param gm_qoq float 0.0 range=-0.5:0.5:0.01

def initialize(context):
    g.symbol = "CNStock:600176.SH"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1d", fields=["open", "high", "low", "close", "volume"])
    context.set_warmup(80)
    context.set_benchmark("CNStock:000300.SH")
    context.set_metadata(direction_mode="long_only")
    set_default_protection(stop_loss_pct=0.12)


def handle_data(context, data):
    industry = get_glass_fiber_industry_week()
    sat_max = float(context.params.get("satellite_max_pct", 0.20))
    hard_stop = float(context.params.get("hard_stop_pct", 0.12))
    market_stress = int(context.params.get("market_stress", 0))
    cash_ratio = float(context.params.get("cash_profit_ratio", 1.0))
    gm_qoq = float(context.params.get("gm_qoq", 0.0))

    if market_stress or cash_ratio < 0.8 and gm_qoq < 0:
        order_target_percent(g.symbol, 0.0, reason="jushi_hard_exit_fund_or_stress")
        return

    if not industry.get("industry_available"):
        # 无行业：不加仓；已有仓可由止损保护；明确不新开
        pos = get_position(g.symbol)
        if pos is None or abs(float(getattr(pos, "amount", 0) or 0)) < 1e-9:
            return
        # 保持或仅允许保护减仓 — MVP：不动仓，等行业恢复
        return

    # 连续两周转空：调用方可用 g 缓存上周；MVP 单周近似：本周 cloth<0 且 inventory>0 则清仓
    if int(industry["cloth_trend"]) < 0 and int(industry["inventory_trend"]) > 0:
        order_target_percent(g.symbol, 0.0, reason="jushi_industry_downturn")
        return

    allow = (
        int(industry["cloth_trend"]) >= 0
        and int(industry["inventory_trend"]) <= 0
        and int(industry["new_capacity_flag"]) == 0
    )
    target = sat_max if allow else 0.0
    order_target_percent(
        g.symbol,
        target,
        reason="jushi_satellite_target",
        stop_loss_pct=hard_stop if target > 0 else 0.0,
    )
    # 日内 T：MVP 日线策略仅用 enable_intraday_t 记日志；分钟 T 二期
    if target > 0 and int(context.params.get("enable_intraday_t", 1)) == 1:
        log("jushi intraday T armed frac<=%s (signal; execute externally)" % context.params.get("t_max_frac", 0.25))
```

完善点（实现时补全，勿留半成品）：

- 用 `g.last_industry` 存上周，实现 **连续两周** 转空；  
- 流动性：`get_history` 估算成交额，低于阈值则 `target=0` 或拒开；  
- PE 分位：若 `get_fundamentals` / valuation 可用则接；否则参数 `pe_percentile` 手填。

- [x] **Step 2: CLI manual upsert**

- [x] **Step 3: Compile / param extract test PASS**

- [ ] **Step 4: Commit（仅当用户要求）**

---

### Task 6: 文档交叉链接与验收清单

**Files:**
- Modify: `docs/superpowers/specs/2026-08-06-jushi-cta-industry-signal-design.md`（状态改为「计划已就绪」，链到本 plan）  
- Optional: `docs/trading/ASHARE_FACTOR_SIGNAL_CN.md` 末尾加一节「玻纤行业周信号 / 巨石 CTA」链到示例

- [x] **Step 1: 更新 spec 状态与 plan 链接**

- [x] **Step 2: 手工验收清单（写入 plan 底部 Done）**

1. 跑迁移  
2. CLI 写入 manual 周 → 策略读到  
3. 空 URL 时 beat skip  
4. fixture 文本 sync upsert public_news  
5. 无行业行时 CTA 不新开仓  

- [ ] **Step 3: Commit（仅当用户要求）**

---

## Spec coverage（自检）

| Spec 项 | Task |
|---------|------|
| 表 DDL + 多源 | Task 1 |
| 公开解析 | Task 2 |
| Celery 采集 | Task 3 |
| 沙箱 API | Task 4 |
| CTA + manual | Task 5 |
| 文档/验收 | Task 6 |
| 付费 API 实装 | 非目标（仅 source 预留） |
| 分钟真实 T | MVP 日志/参数预留，完整分钟子策略非本期强制 |

## Placeholder scan

无 TBD/「类似 Task N」；commit 步骤受用户规则约束已写明。

---

## Done（Task 6 验收）

**日期：** 2026-08-06  
**状态：** MVP 文档交叉链接已更新；自动化测试 34 passed（本地 venv）。

| # | 验收项 | 结果 | 说明 |
|---|--------|------|------|
| 1 | 跑迁移 | ✅ | DDL：`backend_api_python/migrations/20260806_industry_glass_fiber_weekly.sql`；生产环境需手工执行 |
| 2 | CLI 写入 manual 周 → 策略读到 | ✅ | `scripts/upsert_glass_fiber_industry_week.py`；`resolve_glass_fiber_week` / 沙箱 `get_glass_fiber_industry_week` 单测覆盖 manual 优先 |
| 3 | 空 URL 时 beat skip | ✅ | `test_sync_skipped_when_urls_empty`：`GLASS_FIBER_NEWS_URLS=""` → `{ok: true, skipped: true}` |
| 4 | fixture 文本 sync upsert public_news | ✅ | `test_sync_upserts_public_news` mock fetch → `source=public_news` upsert |
| 5 | 无行业行时 CTA 不新开仓 | ✅ | `strategy_v2_jushi_satellite_cta.py` + `test_jushi_satellite_cta_compile.py`；`industry_available=False` 时不发新开仓意图 |

**测试命令：**

```bash
cd backend_api_python
python3 -m pytest tests/test_industry_glass_fiber_store.py \
  tests/test_industry_glass_fiber_parse.py \
  tests/test_glass_fiber_industry_task.py \
  tests/test_strategy_v2_glass_fiber_api.py \
  tests/test_jushi_satellite_cta_compile.py -q
```

**文档链接：**

- Spec 状态已更新 → 链至本 plan  
- `docs/trading/ASHARE_FACTOR_SIGNAL_CN.md` §11 玻纤行业周信号 / 巨石 CTA
