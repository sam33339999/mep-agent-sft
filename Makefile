# MEP Agent SFT：常用指令。直接輸入 `make` 或 `make help` 看清單。
#
# 參數都可以在指令列覆蓋，例如：
#   make serve PORT=9000
#   make generate N=500 SEED=7 DIFFICULTY=3

UV         ?= uv
PY         := $(UV) run python -m mep_sft

# 網站
HOST       ?= 127.0.0.1
PORT       ?= 8000
DB         ?= data/review.db

# 資料產生
N          ?= 200
SEED       ?= 0
DIFFICULTY ?=
RAW        ?= data/raw.jsonl
TRAIN      ?= data/train.jsonl

.DEFAULT_GOAL := help
.PHONY: help install install-all serve generate assemble validate eval-gold data demo ifc \
        test test-fast lint format clean

help: ## 顯示這份指令清單
	@echo "MEP Agent SFT"
	@echo ""
	@echo "用法: make <目標> [參數=值]"
	@echo ""
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "常用參數 (預設值):  HOST=$(HOST)  PORT=$(PORT)  DB=$(DB)"
	@echo "                    N=$(N)  SEED=$(SEED)  DIFFICULTY=<空=混合 1-4>"
	@echo ""
	@echo "範例:  make serve PORT=9000"
	@echo "       make data N=500 SEED=7 DIFFICULTY=3"

install: ## 安裝依賴 (核心 + 測試工具)
	$(UV) sync --extra dev

install-all: ## 安裝全部選配 (IFC 輸出、瀏覽器測試、HF datasets 檢查)
	$(UV) sync --extra dev --extra ifc --extra e2e --extra trainfmt

serve: ## 啟動審核網站 -> http://HOST:PORT (預設 http://127.0.0.1:8000)
	@echo "網站: http://$(HOST):$(PORT)    資料庫: $(DB)    (Ctrl+C 結束)"
	$(PY) serve --host $(HOST) --port $(PORT) --db $(DB)

generate: ## 產生場景 + solver 解答 -> data/raw.jsonl  (N, SEED, DIFFICULTY)
	$(PY) generate --n $(N) --seed $(SEED) $(if $(DIFFICULTY),--difficulty $(DIFFICULTY),) --out $(RAW)

assemble: ## 把 data/raw.jsonl 組裝成訓練用 JSONL -> data/train.jsonl
	$(PY) assemble --raw $(RAW) --out $(TRAIN)

validate: ## 檢查訓練 JSONL 格式 (含 HF datasets 載入) 並寫出 LLaMA-Factory 設定
	$(PY) validate --train $(TRAIN) --dataset-info $(dir $(TRAIN))dataset_info.json

eval-gold: ## 用標準答案自我檢查評測腳本 (應為 100%)
	$(PY) eval --raw $(RAW) --gold

data: generate assemble validate ## 一次做完：產生 -> 組裝 -> 驗證

demo: ## 跑最小範例，輸出 out/demo/scene.html (瀏覽器開啟，可旋轉)
	$(PY) demo --case two-pipes --out out/demo

ifc: ## 把示範場景輸出成 IFC -> out/scene.ifc (需 make install-all)
	$(PY) ifc --case opening --out out/scene.ifc

test: ## 跑完整測試 (含真瀏覽器的 e2e，約 2~3 分鐘)
	$(UV) run pytest -q

test-fast: ## 跑測試但略過瀏覽器 e2e (快很多)
	$(UV) run pytest -q --ignore=tests/test_e2e_ui.py

lint: ## 程式碼風格檢查 (ruff)
	$(UV) run ruff check .

format: ## 自動修正可修的風格問題 (ruff --fix)
	$(UV) run ruff check . --fix

clean: ## 刪除暫存與輸出 (out/、快取)；不會動 data/ 與資料庫
	rm -rf out .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -not -path "./.venv/*" -prune -exec rm -rf {} +
