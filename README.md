# MEP Agent SFT 資料生成

程式自動產生「有問題的 MEP 場景」→ 確定性 solver 求標準答案 → 組裝成 SFT 對話 JSONL，
並提供網頁審核（核准後才進樣本池）、看圖學習、3D 檢視與評測。規格見 `SPEC.md`，計畫與決策見 `PLAN.md`。

## 安裝

```bash
uv sync --extra dev            # 核心 + 測試
uv sync --extra dev --extra ifc --extra e2e --extra trainfmt  # 另加 IFC、瀏覽器測試、HF datasets 載入檢查（選配）
```

## 常用指令

```bash
uv run python -m mep_sft demo --case two-pipes --out out/demo   # 最小範例 → out/demo/scene.html
uv run python -m mep_sft generate --n 200 --seed 0 --out data/raw.jsonl   # 場景 + solver 解
uv run python -m mep_sft assemble --raw data/raw.jsonl --out data/train.jsonl   # 訓練 JSONL
uv run python -m mep_sft validate --train data/train.jsonl --dataset-info data/dataset_info.json   # 格式檢查 + HF datasets 載入
uv run python -m mep_sft ifc --case opening --out out/opening.ifc   # IFC 輸出（需 ifc extra）
uv run python -m mep_sft eval --raw data/raw.jsonl --gold        # 標準答案自測（應為 100%）
uv run python -m mep_sft eval --raw data/raw.jsonl --pred preds.jsonl   # 替模型輸出打分
uv run python -m mep_sft serve --port 8000                       # 審核網站
uv run pytest                                                    # 測試
```

`generate` 的 `--difficulty 1..4` 控制管數、系統數、問題數、牆開口與兩端固定管；省略則混合。
同 `--seed` 輸出 byte 一致。

## 資料流

```
generator → scene ─┬→ rules → violations ─┐
                   └→ solver → fixed ─────┴→ assembler → JSONL
                                              ↑
                       審核網站（核准 / 退回 / 人工微調）→ 匯出已核准樣本
```

- `rules.py`：唯一的裁判（碰撞、穿牆、淨高、排水坡度）。生成器、solver、審核閘門、評測都用它。
- `solver.py`：依序嘗試垂直位移 → 水平平移 → 繞道 → 重做坡度，只接受讓違規數嚴格減少的方案。
- `assembler.py`：`<think>` 由 solver trace 模板化產生，題型有 `fix_full` / `fix_direct` / `detect_only`。
  答案最後有 ```json 區塊（`answer.py` 負責寫入與解析）。

## 審核網站（`serve`）

- 產生樣本 → 待審 → 核准 / 退回；可改修正座標與文字，每次修改存成新版本（`source=human`）。
- 核准閘門：答案內的 json 套用後必須零違規、`<think>` 結構正確、文字與修正一致；否則需 override 並填原因。
- 匯出 `/api/export`：只含已核准樣本，`meta.source` 標示 `auto` / `human`（`?include_human=false` 可排除）。
- 看圖學習：平面圖＋立面圖、編號標記、違規白話解說、逐步導覽、測驗模式、名詞表。
- `/viewer?id=樣本編號`：Three.js 3D 檢視器（修正前→後滑桿、真實圓管、牆開口）。

## 餵給訓練框架

`validate` 會逐筆檢查結構（角色順序、`<think>` 恰好一組），再用 HF `datasets.load_dataset("json")` 實際載入
（LLaMA-Factory、Axolotl、Unsloth 都是這條路）。`--dataset-info` 會寫出 LLaMA-Factory 用的設定片段
（OpenAI messages 格式，`formatting: sharegpt`）。`assemble --no-meta` 可輸出只含 `messages` 的檔案。

### 已實際驗證過的訓練流程

用 LLaMA-Factory 0.9.5 讀取 200 筆樣本（`qwen3` 範本），平均 1323 個 token、最長 3024、沒有被截斷，loss
只算 assistant 部分（從 `<think>` 開始）。接著在 RTX 3090 上用 Qwen3-0.6B + LoRA 跑 20 步（約 33 秒），
loss 由 1.17 降到 0.30，adapter 正常存檔。**這只證明資料能走完整條訓練路徑，並不代表模型學會了任務**；
要知道訓練有沒有用，請用 `eval` 對訓練後的模型輸出打分。設定檔見 `examples/llamafactory_sft.yaml`。

## 多樓層

難度 4 會建兩層樓（每層 3.2 m 一層）。管線有所屬樓層，淨高與天花板限制從該層樓板起算；題目文字會列出
各層的絕對標高限制。平面圖會把各層疊在一起顯示。

## 已知限制

- 牆穿透的修法有「沿牆滑到開口」與「在牆前截短（保留較長一側，至少 0.5 m）」；沒有「新增開口」這個動作。
- 牆開口只在穿牆中點檢查；斜穿牆可能有誤差。
- 難度 3、4 的 solver 解出率約 76%、59%；其餘是擁擠到無法收斂的場景，會被丟棄並記錄在統計。
- 新增牆開口只允許用在標示「可新增開口」的牆（內牆）；外牆視為結構牆，不能新增。
- IFC 已驗證：ifcopenshell 幾何引擎比對座標與開口體積；也用 Bonsai 0.8.4（BlenderBIM 的新版）在 headless Blender 5.0.1 裡以它自己的 `bim.load_project` 載入，牆的開口體積為 5.480 m³（6.0 − 0.52）、管線與兩層樓牆的世界座標包圍盒與場景一致（`examples/bonsai_check.py`）。這是無介面的載入檢查，不是在 Blender 圖形介面裡手動開啟。
