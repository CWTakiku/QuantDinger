# 沪深300指增 QP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地沪深300指增 MVP：因子预处理（去极值/标准化/中性化）、线性等权 Alpha、简化 QP 优化器、Strategy V2 周频主调仓示例。

**Architecture:** 后端 `app/services/csi300_enhanced/` 提供预处理与优化；通过 Strategy V2 runtime 注入 `optimize_enhanced_index`；示例策略日频算分、周频调仓；Tushare 资金流/一致预期同步为后续任务。

**Tech Stack:** Python、numpy、pandas、Strategy API V2、Tushare（后续）、pytest

## Global Constraints

- 宇宙 `csi300`，基准 `CNStock:000300.SH`，long_only  
- A 股整手规则保留  
- 策略沙箱仅允许 numpy/pandas；QP 必须在平台侧实现并注入 API  
- MVP 不用 cvxpy/scipy；用对角风险 + 投影迭代求解  
- 文档与注释：策略源码标识符英文；设计/计划中文可  

---

### Task 1: 因子预处理模块

**Files:**
- Create: `backend_api_python/app/services/csi300_enhanced/__init__.py`
- Create: `backend_api_python/app/services/csi300_enhanced/preprocess.py`
- Test: `backend_api_python/tests/test_csi300_enhanced_preprocess.py`

**Produces:**
- `winsorize_mad(series, n_mad=5) -> Series`
- `cross_section_zscore(series) -> Series`
- `neutralize_industry_size(values, industry, log_mcap) -> Series`
- `build_equal_weight_alpha(factor_frame, signs) -> Series`

- [x] 写失败测试并实现预处理
- [x] pytest 通过
- [ ] Commit

### Task 2: 简化指增 QP 优化器

**Files:**
- Create: `backend_api_python/app/services/csi300_enhanced/optimizer.py`
- Test: `backend_api_python/tests/test_csi300_enhanced_optimizer.py`

**Produces:**
- `optimize_enhanced_index(alpha, w_bench, *, w_prev=None, active_limit=0.025, industry=None, industry_limit=0.05, risk_aversion=1.0, turn_penalty=0.01, te_var_limit=None) -> dict`  
  返回 `{weights, status, turnover, active_risk_proxy}`

- [x] 写失败测试（满仓、非负、主动权重盒约束）
- [x] 对角 Σ 投影梯度 / 交替投影实现
- [x] pytest 通过
- [ ] Commit

### Task 3: Runtime 注入 API

**Files:**
- Modify: `backend_api_python/app/services/strategy_v2/runtime.py`（注入 globals）
- Test: `backend_api_python/tests/test_csi300_enhanced_runtime_api.py`

**Produces:** 策略内可调用 `optimize_enhanced_index(...)`

- [x] 注入并写契约/冒烟测试
- [ ] Commit

### Task 4: 示例策略（量价 MVP）

**Files:**
- Create: `docs/examples/strategy_v2_csi300_enhanced_weekly.py`
- Optional seed: template SQL 若项目惯例需要

**Behavior:**
- `set_universe(pool="csi300")`, benchmark 000300  
- 日频：用 momentum 60/120 + realized vol 等合成 alpha（等权）  
- 周频：`optimize_enhanced_index` → `order_target_percent`  
- 换手阈值跳过  

- [x] 策略通过 `compile_strategy_v2`
- [ ] Commit

### Task 5: Tushare 同步骨架（表 + client stubs）

**Files:**
- Create: `backend_api_python/migrations/20260731_csi300_enhanced_factor_store.sql`
- Create: `backend_api_python/app/services/csi300_enhanced/tushare_sync.py`（index_weight / daily_basic 拉取骨架）
- Test: mock 拉取权重解析

- [x] 迁移 SQL + 同步函数骨架
- [ ] Commit

---

## 后续（本计划后）

- 北向/融资/一致预期字段接入与层权重  
- ICIR / L2 T+5  
- 完整行业约束与 TE 硬约束  
- LightGBM 辅助 Alpha  
