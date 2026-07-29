# QuantDinger 源码开发启动（Windows）
# 依赖：Docker 中 postgres/redis/redis-jobs 已运行；本脚本只起本机后端 + Vue。
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Backend = Join-Path $Root "backend_api_python"
$Vue = Join-Path $Root "QuantDinger-Vue"
$Py = Join-Path $Backend ".venv\Scripts\python.exe"

if (-not (Test-Path $Py)) { throw "缺少后端 venv，请先在 backend_api_python 执行: python -m venv .venv && .venv\Scripts\pip install -r requirements.txt" }
if (-not (Test-Path (Join-Path $Vue "node_modules"))) { throw "缺少前端依赖，请先在 QuantDinger-Vue 执行: npm install" }

Write-Host "启动后端 http://127.0.0.1:5000 …"
Start-Process -WorkingDirectory $Backend -FilePath $Py -ArgumentList "run.py"

Write-Host "启动前端 http://127.0.0.1:8000 …"
$npm = (Get-Command npm -ErrorAction SilentlyContinue)?.Source
if (-not $npm) { $env:Path = "K:\work\TradingAgents\.tools\node;" + $env:Path }
Set-Location $Vue
npm run dev -- --host 0.0.0.0 --port 8000
