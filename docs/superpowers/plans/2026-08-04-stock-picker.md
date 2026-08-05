# 选股模块 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在研究工厂前增加 admin「选股」页：列出已发布量化模型、展示组成（因子清单 + 学习器）、按模型 ensure 分数、预览 Top-N 选股名单。

**Architecture:** 薄 API 包装已有 `get_quant_model` + bridge `session_detail` 装配 `composition`；`ensure-scores` 委托 `ensure_quant_model_scores`；Top-N 复用 `GET /api/rdagent/alpha-preview`（`limit`/`order`）。Vue 新页三栏，不搬迁研究工厂发布流。

**Tech Stack:** Flask Human API、RdAgentBridgeClient、pytest、Vue 2、Ant Design Vue

**Spec:** `docs/superpowers/specs/2026-08-04-stock-picker-design.md`

## Global Constraints

- 权限：选股页与新增 API 均为 **admin only**（`@login_required` + `@admin_required`）
- 组成 = 因子清单 + 学习器身份；**不是**成交持仓
- 单个已发布模型 `kind` 仅为 `factor` **或** `model`；Session 内可先后有两类 Loop
- Bridge 不可达时详情仍返回模型元数据，`composition.available=false`
- ensure 仅允许 `status=published`；复用 `ensure_quant_model_scores`，禁止沙箱内懒推理
- Top-N 默认 **30**，UI 可调 5–100；preview 用已有 alpha-preview
- Commit message：中文 Conventional Commits；**仅在用户明确要求时 git commit**
- 不在选股页做发布/归档

## File Map

| 文件 | 职责 |
|------|------|
| `backend_api_python/app/services/quant_models/composition.py` | 从 bridge session detail 装配 composition |
| `backend_api_python/app/services/quant_models/__init__.py` | 导出新符号 |
| `backend_api_python/app/routes/quant_models.py` | `GET /<key>`、`POST /<key>/ensure-scores` |
| `backend_api_python/tests/test_quant_model_composition.py` | composition 单测 |
| `backend_api_python/tests/test_quant_models_routes.py` | 路由单测扩展 |
| `QuantDinger-Vue/src/api/quantModels.js` | `fetchQuantModel`、`ensureQuantModelScores` |
| `QuantDinger-Vue/src/views/stock-picker/index.vue` | 选股页 |
| `QuantDinger-Vue/src/config/router.config.js` | `/stock-picker` 在 `/rdagent` 前 |
| `QuantDinger-Vue/src/locales/lang/zh-CN.js`（及 en / strategy-v2 若需要） | 菜单与文案 |
| `docs/superpowers/specs/2026-08-04-stock-picker-design.md` | 状态改为已确认 |

---

### Task 1: composition 装配服务

**Files:**
- Create: `backend_api_python/app/services/quant_models/composition.py`
- Modify: `backend_api_python/app/services/quant_models/__init__.py`
- Test: `backend_api_python/tests/test_quant_model_composition.py`

**Interfaces:**
- Consumes: `RdAgentBridgeClient.session_detail(session_id, include=...)`；`RdAgentBridgeError`
- Produces:
  - `build_quant_model_composition(model: dict, *, detail_fetcher=None) -> dict`
  - 返回形状：`{available, kind, session_id, loop_index, learner, factors, bridge_error}`

- [ ] **Step 1: Write the failing test**

```python
# backend_api_python/tests/test_quant_model_composition.py
from app.services.quant_models.composition import build_quant_model_composition


def test_build_composition_factor_loop():
    model = {
        "kind": "factor",
        "provenance_json": {"session_id": "sess-1", "loop_index": 2, "mode": "factor"},
    }

    def fake_detail(session_id, include=None):
        assert session_id == "sess-1"
        return {
            "loops": [
                {
                    "loop_index": 2,
                    "kind": "factor",
                    "artifacts": [
                        {
                            "kind": "factor",
                            "name": "mom20",
                            "formulation": "close/close_20-1",
                            "description": "momentum",
                        }
                    ],
                    "factors": [
                        {
                            "kind": "factor",
                            "name": "mom20",
                            "formulation": "close/close_20-1",
                            "description": "momentum",
                        }
                    ],
                }
            ],
            "sota_library": [],
        }

    out = build_quant_model_composition(model, detail_fetcher=fake_detail)
    assert out["available"] is True
    assert out["kind"] == "factor"
    assert out["learner"] is None
    assert out["factors"][0]["name"] == "mom20"
    assert out["bridge_error"] is None


def test_build_composition_model_loop_with_sota_factors():
    model = {
        "kind": "model",
        "provenance_json": {"session_id": "sess-1", "loop_index": 5, "mode": "model"},
    }

    def fake_detail(session_id, include=None):
        return {
            "loops": [
                {
                    "loop_index": 5,
                    "kind": "model",
                    "artifacts": [
                        {
                            "kind": "model",
                            "name": "lstm_v2",
                            "model_type": "pytorch",
                            "architecture": "LSTM",
                            "hyperparameters": {"lr": 0.01},
                        }
                    ],
                }
            ],
            "sota_library": [
                {"name": "f1", "formulation": "a", "description": "d1"},
            ],
            "sota_model": {
                "name": "lstm_v2",
                "architecture": "LSTM",
                "model_type": "pytorch",
                "loop_index": 5,
            },
        }

    out = build_quant_model_composition(model, detail_fetcher=fake_detail)
    assert out["learner"]["model_type"] == "pytorch"
    assert out["learner"]["architecture"] == "LSTM"
    assert out["factors"][0]["name"] == "f1"


def test_build_composition_bridge_down():
    from app.services.rdagent_bridge.errors import RdAgentBridgeError

    model = {
        "kind": "model",
        "provenance_json": {"session_id": "sess-1", "loop_index": 0, "mode": "model"},
    }

    def boom(session_id, include=None):
        raise RdAgentBridgeError(503, "rdagent_bridge_unreachable", "无法连接 rdagent bridge，请先在本机启动 rdagent-bridge")

    out = build_quant_model_composition(model, detail_fetcher=boom)
    assert out["available"] is False
    assert out["learner"] is None
    assert out["factors"] == []
    assert "bridge" in (out["bridge_error"] or "").lower() or "rdagent" in (out["bridge_error"] or "").lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd QuantDinger/backend_api_python && python -m pytest tests/test_quant_model_composition.py -v
```

Expected: FAIL（模块/函数不存在）

- [ ] **Step 3: Write minimal implementation**

```python
# backend_api_python/app/services/quant_models/composition.py
from __future__ import annotations

from typing import Any, Callable

from app.services.rdagent_bridge.client import RdAgentBridgeClient
from app.services.rdagent_bridge.errors import RdAgentBridgeError


def _empty(kind: str, session_id: str, loop_index: int | None, error: str | None) -> dict[str, Any]:
    return {
        "available": error is None,
        "kind": kind,
        "session_id": session_id,
        "loop_index": loop_index,
        "learner": None,
        "factors": [],
        "bridge_error": error,
    }


def _factor_rows(items: list[Any] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if item.get("kind") and item.get("kind") != "factor":
            continue
        rows.append(
            {
                "name": item.get("name"),
                "formulation": item.get("formulation"),
                "description": item.get("description"),
            }
        )
    return rows


def _learner_from_artifact(artifact: dict[str, Any] | None) -> dict[str, Any] | None:
    if not artifact:
        return None
    return {
        "name": artifact.get("name"),
        "model_type": artifact.get("model_type"),
        "architecture": artifact.get("architecture"),
        "hyperparameters": artifact.get("hyperparameters")
        if isinstance(artifact.get("hyperparameters"), dict)
        else {},
    }


def build_quant_model_composition(
    model: dict[str, Any],
    *,
    detail_fetcher: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    provenance = model.get("provenance_json") or {}
    if not isinstance(provenance, dict):
        provenance = {}
    session_id = str(provenance.get("session_id") or "").strip()
    loop_raw = provenance.get("loop_index")
    loop_index = int(loop_raw) if loop_raw is not None and str(loop_raw).strip() != "" else None
    kind = str(model.get("kind") or provenance.get("mode") or "factor").strip().lower() or "factor"

    if not session_id or loop_index is None:
        return _empty(kind, session_id, loop_index, "量化模型缺少 session_id/loop_index 溯源")

    fetcher = detail_fetcher
    if fetcher is None:
        client = RdAgentBridgeClient.from_env()

        def fetcher(session_id: str, include: str | None = None):
            return client.session_detail(session_id, include=include)

    try:
        detail = fetcher(session_id, include="summary,loops,factors,sota_library")
    except RdAgentBridgeError as exc:
        return _empty(kind, session_id, loop_index, str(exc) or "无法连接 rdagent bridge")
    except Exception as exc:  # noqa: BLE001 — surface as bridge_error
        return _empty(kind, session_id, loop_index, str(exc)[:240])

    loops = detail.get("loops") if isinstance(detail, dict) else None
    loop = None
    for item in loops or []:
        if isinstance(item, dict) and int(item.get("loop_index")) == int(loop_index):
            loop = item
            break

    factors: list[dict[str, Any]] = []
    learner = None
    if loop:
        artifacts = loop.get("artifacts") or loop.get("factors") or []
        if kind == "factor":
            factors = _factor_rows(artifacts)
            learner = None
        else:
            model_art = next(
                (a for a in artifacts if isinstance(a, dict) and a.get("kind") == "model"),
                None,
            )
            learner = _learner_from_artifact(model_art)
            factors = _factor_rows(artifacts)
            if not factors:
                factors = _factor_rows(detail.get("sota_library") if isinstance(detail, dict) else None)

    return {
        "available": True,
        "kind": kind,
        "session_id": session_id,
        "loop_index": loop_index,
        "learner": learner,
        "factors": factors,
        "bridge_error": None,
    }
```

在 `__init__.py` 增加导出 `build_quant_model_composition`。

- [ ] **Step 4: Run test to verify it passes**

```bash
cd QuantDinger/backend_api_python && python -m pytest tests/test_quant_model_composition.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**（仅当用户要求）

```bash
git add backend_api_python/app/services/quant_models/composition.py \
  backend_api_python/app/services/quant_models/__init__.py \
  backend_api_python/tests/test_quant_model_composition.py
# git commit -m "feat: 装配量化模型组成摘要"
```

---

### Task 2: GET 详情 + POST ensure-scores 路由

**Files:**
- Modify: `backend_api_python/app/routes/quant_models.py`
- Modify: `backend_api_python/tests/test_quant_models_routes.py`

**Interfaces:**
- Consumes: `get_quant_model`, `build_quant_model_composition`, `ensure_quant_model_scores`
- Produces:
  - `GET /api/quant-models/<model_key>` → `{code:1, data: {…model fields, composition}}`
  - `POST /api/quant-models/<model_key>/ensure-scores` body `{as_ofs?: string[], start?: string, end?: string}`

- [ ] **Step 1: Write the failing route tests**

在 `test_quant_models_routes.py` 追加：

```python
def test_get_model_detail_includes_composition(client, monkeypatch):
    monkeypatch.setattr(
        "app.routes.quant_models.get_quant_model",
        lambda key: {
            "model_key": key,
            "display_name": "M1",
            "kind": "factor",
            "status": "published",
            "alpha_source": "rdagent",
            "alpha_version": "qm_m1",
            "universe": "csi300",
            "provenance_json": {"session_id": "s1", "loop_index": 1, "mode": "factor"},
            "metrics_json": {},
        },
    )
    monkeypatch.setattr(
        "app.routes.quant_models.build_quant_model_composition",
        lambda model, detail_fetcher=None: {
            "available": True,
            "kind": "factor",
            "session_id": "s1",
            "loop_index": 1,
            "learner": None,
            "factors": [{"name": "f1", "formulation": "x", "description": "d"}],
            "bridge_error": None,
        },
    )
    resp = client.get("/api/quant-models/m1", headers=_admin_auth_headers(monkeypatch))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["code"] == 1
    assert body["data"]["composition"]["factors"][0]["name"] == "f1"


def test_get_model_detail_404(client, monkeypatch):
    monkeypatch.setattr("app.routes.quant_models.get_quant_model", lambda key: None)
    resp = client.get("/api/quant-models/missing", headers=_admin_auth_headers(monkeypatch))
    assert resp.status_code == 404


def test_ensure_scores_published_only(client, monkeypatch):
    calls = {}

    monkeypatch.setattr(
        "app.routes.quant_models.get_quant_model",
        lambda key: {
            "model_key": key,
            "status": "published",
            "kind": "model",
            "alpha_source": "rdagent",
            "alpha_version": "qm_m1",
            "universe": "csi300",
            "provenance_json": {"session_id": "s1", "loop_index": 0, "mode": "model"},
        },
    )

    def fake_ensure(model, as_ofs, on_progress=None):
        calls["as_ofs"] = [d.isoformat() if hasattr(d, "isoformat") else str(d) for d in as_ofs]
        return {"missing_before": calls["as_ofs"], "inferred": 1, "still_missing": []}

    monkeypatch.setattr("app.routes.quant_models.ensure_quant_model_scores", fake_ensure)
    resp = client.post(
        "/api/quant-models/m1/ensure-scores",
        json={"as_ofs": ["2026-04-10"]},
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 200
    assert calls["as_ofs"] == ["2026-04-10"]


def test_ensure_scores_rejects_archived(client, monkeypatch):
    monkeypatch.setattr(
        "app.routes.quant_models.get_quant_model",
        lambda key: {"model_key": key, "status": "archived", "provenance_json": {}},
    )
    resp = client.post(
        "/api/quant-models/m1/ensure-scores",
        json={"as_ofs": ["2026-04-10"]},
        headers=_admin_auth_headers(monkeypatch),
    )
    assert resp.status_code == 400
```

- [ ] **Step 2: Run tests — expect FAIL**（路由未注册）

```bash
cd QuantDinger/backend_api_python && python -m pytest tests/test_quant_models_routes.py -k "get_model_detail or ensure_scores" -v
```

- [ ] **Step 3: Implement routes**

在 `quant_models.py` 增加导入与辅助，并**注意路由顺序**：`/<model_key>/archive` 与新路由并存时，把更具体的 `ensure-scores` 写在通用 `<model_key>` 之前或使用完整路径（Flask 静态段优先，`/<key>/ensure-scores` 与 `/<key>/archive` 同级即可）。

```python
from datetime import date, datetime, timedelta

from app.services.quant_models import (
    archive_quant_model,
    get_quant_model,
    list_quant_models,
    publish_quant_model,
)
from app.services.quant_models.composition import build_quant_model_composition
from app.services.quant_models.ensure_scores import ensure_quant_model_scores
from app.services.rdagent_bridge.errors import RdAgentBridgeError


def _parse_day(value: object) -> date:
    text = str(value or "").strip()[:10]
    return datetime.strptime(text, "%Y-%m-%d").date()


def _as_ofs_from_body(payload: dict) -> list[date]:
    raw_list = payload.get("as_ofs")
    if isinstance(raw_list, list) and raw_list:
        return sorted({_parse_day(item) for item in raw_list})
    start = payload.get("start")
    end = payload.get("end")
    if start and end:
        a = _parse_day(start)
        b = _parse_day(end)
        if b < a:
            raise ValueError("end must be >= start")
        out: list[date] = []
        cur = a
        # Cap calendar expansion to 400 days to avoid accidental huge ranges
        for _ in range(400):
            out.append(cur)
            if cur >= b:
                break
            cur = cur + timedelta(days=1)
        return out
    raise ValueError("as_ofs or start/end required")


@quant_models_blp.route("/<string:model_key>", methods=["GET"], strict_slashes=False)
@login_required
@admin_required
def get_model(model_key: str):
    model = get_quant_model(model_key)
    if not model:
        return jsonify({"code": 0, "msg": "quantModels.notFound", "data": None}), 404
    composition = build_quant_model_composition(model)
    data = dict(model)
    data["composition"] = composition
    return _success(data)


@quant_models_blp.route("/<string:model_key>/ensure-scores", methods=["POST"])
@login_required
@admin_required
def ensure_model_scores(model_key: str):
    model = get_quant_model(model_key)
    if not model:
        return jsonify({"code": 0, "msg": "quantModels.notFound", "data": None}), 404
    if str(model.get("status") or "").strip() != "published":
        return jsonify({"code": 0, "msg": "quantModels.notPublished", "data": None}), 400
    payload = request.get_json(silent=True) or {}
    try:
        as_ofs = _as_ofs_from_body(payload)
    except ValueError as exc:
        return jsonify({"code": 0, "msg": str(exc), "data": None}), 400
    try:
        result = ensure_quant_model_scores(model, as_ofs)
    except RdAgentBridgeError as exc:
        return jsonify({"code": 0, "msg": str(exc), "data": None}), int(getattr(exc, "status_code", None) or 503)
    except Exception as exc:
        logger.exception("ensure quant model scores failed")
        return jsonify({"code": 0, "msg": str(exc)[:240] or "quantModels.ensureFailed", "data": None}), 500
    return _success(result)
```

确认 `RdAgentBridgeError` 是否有 `status_code` 属性；若为 `.status` / 位置参数，按 `errors.py` 实际字段取值。

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd QuantDinger/backend_api_python && python -m pytest tests/test_quant_models_routes.py tests/test_quant_model_composition.py -v
```

- [ ] **Step 5: Commit**（仅当用户要求）

---

### Task 3: 前端 API + 路由 + 文案

**Files:**
- Modify: `QuantDinger-Vue/src/api/quantModels.js`
- Modify: `QuantDinger-Vue/src/config/router.config.js`
- Modify: `QuantDinger-Vue/src/locales/lang/zh-CN.js`
- Modify: `QuantDinger-Vue/src/locales/lang/en-US.js`（对称英文）

- [ ] **Step 1: 扩展 `quantModels.js`**

```javascript
export function fetchQuantModel (modelKey) {
  return request({
    url: `/api/quant-models/${encodeURIComponent(modelKey)}`,
    method: 'get'
  })
}

export function ensureQuantModelScores (modelKey, data) {
  return request({
    url: `/api/quant-models/${encodeURIComponent(modelKey)}/ensure-scores`,
    method: 'post',
    data,
    timeout: 300000
  })
}
```

保留已有 `fetchQuantModels` 的尾斜杠 `/api/quant-models/`。

- [ ] **Step 2: 在 `router.config.js` 于 `/rdagent` 之前插入**

```javascript
{
  path: '/stock-picker',
  name: 'StockPicker',
  component: () => import('@/views/stock-picker'),
  meta: { title: 'menu.dashboard.stockPicker', keepAlive: false, icon: 'fund', permission: ['admin'] }
},
```

- [ ] **Step 3: 文案**

`zh-CN.js`：`"menu.dashboard.stockPicker": "选股"`  
`en-US.js`：`"menu.dashboard.stockPicker": "Stock Picker"`

- [ ] **Step 4: 创建占位页 `src/views/stock-picker/index.vue`**（可先空壳「选股」标题，Task 4 填满）

- [ ] **Step 5: 本地确认菜单顺序**（admin 登录后侧栏「选股」在「研究工厂」之上）

- [ ] **Step 6: Commit**（仅当用户要求）

---

### Task 4: 选股页三栏 UI

**Files:**
- Create/Replace: `QuantDinger-Vue/src/views/stock-picker/index.vue`
- Reuse: `src/api/rdagent.js` → `fetchAlphaPreview`

**Interfaces:**
- Consumes: `fetchQuantModels({ status: 'published' })`, `fetchQuantModel(key)`, `ensureQuantModelScores(key, body)`, `fetchAlphaPreview({ source, version, as_of, limit, order })`

- [ ] **Step 1: 实现页面数据流**

```javascript
// data 关键字段
selectedKey: '',
models: [],
detail: null,
compositionLoading: false,
asOf: undefined,          // 'YYYY-MM-DD'
topN: 30,
previewRows: [],
previewLoading: false,
ensuring: false,
```

加载列表成功后若有数据：`selectedKey = models[0].model_key` 并 `loadDetail()`。

`loadDetail`：`fetchQuantModel(selectedKey)` → `detail`；用 `detail.alpha_source/version`；若已有 `asOf` 则 `loadPreview()`。

`loadPreview`：

```javascript
const res = await fetchAlphaPreview({
  source: this.detail.alpha_source,
  version: this.detail.alpha_version,
  as_of: this.asOf,
  limit: this.topN,
  order: 'desc'
})
// unwrap 与项目一致（res.data / res）
this.previewRows = (payload.rows || []).slice(0, this.topN)
```

`handleEnsure`：若只有单日 `asOf`，body `{ as_ofs: [asOf] }`；若提供起止，传 `{ start, end }`；成功后 `loadPreview()`。

- [ ] **Step 2: 布局**

- 左：`a-table` 或列表点击选中（列：display_name、kind、learner 摘要占位、published_at）  
- 中：若 `composition.available`：  
  - learner 卡（`model_type` / `architecture`）或 Tag「纯因子」  
  - 因子表 name / formulation / description  
  - 否则 Alert `composition.bridge_error`  
- 右：日期选择、`topN` 输入、`刷新分数` 按钮、Top-N 表（rank/symbol/name/score）

错误用 `error.backendMessage || error.message`；Network Error / bridge 文案与研究工厂一致。

- [ ] **Step 3: 空态**

无模型：`a-empty` + 链接到 `/rdagent`（或 hash 路由 `#/rdagent`，与项目路由模式一致）。

- [ ] **Step 4: 手工验证**

1. admin 打开选股，列表有已发布模型。  
2. 选 factor/model 各一，组成字段正确或 bridge 降级。  
3. 选 as_of → 刷新分数 → Top-N 有行。  
4. 非 admin 无菜单。

- [ ] **Step 5: Commit**（仅当用户要求）

---

### Task 5: 文档与联调收尾

**Files:**
- Modify: `docs/superpowers/specs/2026-08-04-stock-picker-design.md`（状态：已确认）
- Optional: `docs/RDAGENT_EXTERNAL_ALPHA_CN.md` 增加「选股」一小节（3–5 行入口说明）

- [ ] **Step 1: 更新 spec 状态为已确认，并指向本 plan**

- [ ] **Step 2: 重建/热更新本地 FE+BE 后冒烟**

```bash
curl -sf http://127.0.0.1:19901/health
# 登录后浏览器打开 http://127.0.0.1:8888/#/stock-picker
```

- [ ] **Step 3: Commit**（仅当用户要求）

---

## Spec coverage self-check

| Spec 要求 | Task |
|-----------|------|
| 侧栏选股在研究工厂前、admin | Task 3 |
| 已发布列表 | Task 3–4 |
| 组成因子+学习器 | Task 1–2, 4 |
| ensure 推理 | Task 2, 4 |
| Top-N 默认 30 | Task 4 |
| Bridge 降级 | Task 1–2, 4 |
| 不在选股发布 | Global / Task 4 不做 |
| 复用 alpha-preview | Task 4 |

## Placeholder scan

无 TBD；路由/函数名与 store/client 现有符号对齐（`get_quant_model`、`ensure_quant_model_scores`、`session_detail`）。
