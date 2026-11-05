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
  貪婪法卡住時才啟動束搜尋（beam search，有節點預算）當備援，所以貪婪法原本解得出的場景結果完全不變；
  候選評估只重算被移動的那條管（`rules.recheck`，與全量檢查等價，有性質測試），約快 5 倍。
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

### 實驗 1：訓練前後的通過率（單次實驗，小規模）

設定見 `examples/llamafactory_exp1.yaml`：700 筆訓練資料、Qwen3-0.6B + LoRA（r=32）、3 個 epoch、
RTX 3090 約 19 分鐘，loss 由 0.98 降到約 0.06。用另外產生、場景不重疊的 100 題（難度 20/33/26/21）
做貪婪解碼，再用 `eval` 打分：

| | 訓練前 | 訓練後 |
|---|---|---|
| 通過率（零違規 / 偵測完全正確） | 1% | **8%** |
| 格式正確率 | 16% | **86%** |
| 修正題平均消除率 | 1.2% | 15.5% |
| 修正題引入新違規 | 0 題 | 34 題 |
| 無違規場景答「無需修正」 | 7% | 27% |

**怎麼解讀：** 訓練確實有效，但幾乎只學會了「答案的格式」。在格式正確的 86 題裡只有 8 題真的解掉問題；
68 道修正題中有 46 題完全沒消除任何違規、34 題還製造出新違規。典型失敗是格式完美，卻改錯管線、座標也是憑空的
（例如題目是 P2 管底差 0.025 m，標準答案是 P2 上移 0.05 m，模型卻輸出改 P1 的路徑）。
也就是說 0.6B 模型在 700 筆資料下學會了「長什麼樣」，沒學會「該動哪一條、動多少」。

**這個實驗不能證明什麼：** 只跑了一次、驗證題只有 100 題、貪婪解碼、模型很小、資料量很少。
它既不證明這份資料集不好，也不證明它足夠好。若要認真比較，建議用更大的模型（例如 1.7B～4B）、
更多資料（生成器可無限產生）、多組 seed，並用 `reward()` 做後續的強化學習。

### 實驗 2：推理文字加入算術推導（A/B，同一批場景與訓練設定）

實驗 1 的失敗是「改錯管線、座標憑空」，所以實驗 2 把 `fix_full` 的 `<think>` 改成逐步算術
（外緣間距與要求的差、管底 = 中心 − 外半徑與要上移多少、坡度 = 高差 ÷ 水平距離與格線取整、截短距離、
新開口大小），每一步的數字都是在該步之前／之後的實際場景上量出來的（重放所有動作會精確回到標準答案，有測試）。
同樣的 700 筆場景、同樣的訓練設定（`examples/llamafactory_exp2.yaml`，訓練 12 分 47 秒），同樣的 100 題驗證集：

| | 實驗 1 | 實驗 2 |
|---|---|---|
| 通過率 | 8%（95% 信賴區間 4.1～15.0%） | **13%**（7.8～21.0%） |
| 格式正確率 | 86% | 90% |
| 無違規場景答「無需修正」 | 27% | 47% |
| 修正題平均消除率 | 15.5% | 18.6% |
| 修正題引入新違規 | 34 題 | 39 題 |
| 偵測題 precision／recall | 13.8%／29.1% | 20.8%／35.6% |

**怎麼解讀：** 逐題配對看，實驗 1 通過的 8 題實驗 2 全都還過，另外多過 5 題，沒有退步；
但不一致的只有 5 題，精確符號檢定 p = 0.06，兩者的信賴區間大幅重疊，所以**只能說方向是進步、證據偏弱**。
推理文字沒有改的 `fix_direct` 也從 4 題進步到 6 題，表示進步不能全部歸功於算術推導。
`fix_full` 只從 3/60 變成 5/60 通過，引入新違規的題數還略增（34 → 39）。
絕對表現仍然很差：0.6B 模型加 700 筆資料，不足以學會「該動哪一條、動多少」。
下一步較有希望的方向是更大的模型、一到兩個數量級更多的資料，再配合 `reward()` 做強化學習；
想確認推理算術的效果，需要多組 seed 與更大的驗證集。

## 多樓層

難度 4 會建兩層樓（每層 3.2 m 一層）。管線有所屬樓層，淨高與天花板限制從該層樓板起算；題目文字會列出
各層的絕對標高限制。平面圖會把各層疊在一起顯示。

## 已知限制

- 牆穿透的修法有「沿牆滑到開口」與「在牆前截短（保留較長一側，至少 0.5 m）」；沒有「新增開口」這個動作。
- 牆開口只在穿牆中點檢查；斜穿牆可能有誤差。
- 難度 3、4 的生成接受率約 87%、89%（同一組 seed 9000、200 筆；舊版貪婪 solver 是 76%、59%）；其餘是擁擠到無法收斂的場景，
  會被丟棄並記錄在統計，不會產出無效樣本。
- 新增牆開口只允許用在標示「可新增開口」的牆（內牆）；外牆視為結構牆，不能新增。
- IFC 已驗證：ifcopenshell 幾何引擎比對座標與開口體積；也用 Bonsai 0.8.4（BlenderBIM 的新版）在 headless Blender 5.0.1 裡以它自己的 `bim.load_project` 載入，牆的開口體積為 5.480 m³（6.0 − 0.52）、管線與兩層樓牆的世界座標包圍盒與場景一致（`examples/bonsai_check.py`）。這是無介面的載入檢查，不是在 Blender 圖形介面裡手動開啟。
