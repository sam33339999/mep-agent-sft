# MEP Agent SFT 資料生成

程式自動產生「有問題的 MEP 場景」→ 確定性 solver 求標準答案 → 組裝成 SFT 對話 JSONL，
並提供網頁審核（核准後才進樣本池）、看圖學習、3D 檢視與評測。規格見 `SPEC.md`，計畫與決策見 `PLAN.md`。

## 安裝

```bash
uv sync --extra dev            # 核心 + 測試
uv sync --extra dev --extra ifc  # 另加 IFC 輸出（選配）
```

## 常用指令

```bash
uv run python -m mep_sft demo --case two-pipes --out out/demo   # 最小範例 → out/demo/scene.html
uv run python -m mep_sft generate --n 200 --seed 0 --out data/raw.jsonl   # 場景 + solver 解
uv run python -m mep_sft assemble --raw data/raw.jsonl --out data/train.jsonl   # 訓練 JSONL
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

## 已知限制

- 未實作多樓層；難度以管數、系統數、問題數、開口與固定端點區分。
- solver 尚無「裁短管線」策略；穿牆的無開口案例可能被解成把整條管平移到牆外。
- 牆開口只在穿牆中點檢查；斜穿牆可能有誤差。
- 難度 4 的 solver 解出率偏低（約 25%），無解場景會被丟棄並記錄在統計。
