# PLAN：MEP Agent SFT 資料生成專案

> 進度：Phase 0–8 已完成（各 phase 一個 commit）。使用方式見 `README.md`。

對應規格見 `SPEC.md`。本文件定義實作順序、架構與驗收標準。

## 0. 目標與範圍

1. 程式自動生成「有問題的 MEP 場景」，由確定性 solver 求出標準答案，組裝成 SFT JSONL（SPEC 全部內容）。
2. 提供 **Web 版審核介面**：人工檢視場景、核准或退回、微調後才進入正式 SFT 樣本池。
3. 檢視器同時是 **看圖學習工具**：協助使用者理解 MEP 圖面（見第 6 節）。

## 1. 設計原則

- **Scene JSON 是唯一介面**：生成器、solver、規則檢查、檢視器、伺服器都只透過帶 `schema_version` 的 JSON 溝通。檢視器可整個替換（Plotly POC → Three.js）。
- **`rules.py` 是唯一裁判**：生成器驗證問題存在、solver 驗證修正有效、審核介面即時檢查人工修改、評測腳本給模型打分，全都用同一份規則。
- **人工修改必須重新過規則**：未通過的樣本不能進正式樣本池（除非明確標記 override 並記錄原因）。
- **可重現**：同 seed、同版本產出 byte 一致；座標規整到 0.05 m 格線；只用 `random.Random(seed)`。
- **淨高定義**：管底標高 ≥ 2.4 m 且管頂 ≤ 天花板。SPEC §5.3 範例（把 B 下移到 1.8 m）與此定義矛盾，實作時改為上移。

## 2. 架構

```
generator → scene.json ─┬→ solver → fixed.json ─┐
                        │                       ├→ assembler → candidate sample
                        └→ rules → violations ──┘            │
                                                             ▼
                      Web 審核（pending → approved / rejected / edited）
                                                             │ approved
                                                             ▼
                                          SFT 樣本池 → export → train.jsonl
```

### 專案結構

```
mep-agent-sft/
  SPEC.md  PLAN.md
  src/mep_sft/
    geometry.py   scene.py   rules.py   generator.py
    solver.py     assembler.py   cli.py
    server/       app.py（FastAPI）  db.py（SQLite）  schemas.py
  web/            檢視器與審核頁（先 Plotly，後 Three.js）
  tests/
  data/           資料庫與匯出檔（gitignore）
```

### 技術選擇

- 後端：Python 3.11+、FastAPI、SQLite（單人審核夠用，之後可換 Postgres）。
- 前端：先用純 HTML + Plotly（POC），之後換 Three.js；兩者都讀同一組 API。
- 測試：pytest；格式：ruff。

## 3. 審核流程與資料模型

### 樣本狀態

```
pending ──approve──→ approved ──→（進入 SFT 樣本池，可匯出）
   │  ╲
   │   edit → 新版本（重跑 rules）→ 回到 pending 或直接 approved
   └──reject──→ rejected（保留紀錄與原因，不匯出）
```

### 資料表（SQLite）

| 表 | 重點欄位 |
|---|---|
| `samples` | id、seed、generator_version、difficulty、issue_types、status、scene_hash |
| `versions` | sample_id、version、scene_json、fixed_json、violations_json、assistant_text、source（auto / human）、created_at |
| `reviews` | sample_id、version、decision、reason、reviewer、created_at |

- 每次修改產生新 `version`，舊版保留，可回溯與比較。
- `approved` 指向某個特定 version。匯出只取 approved 的那一版。
- `scene_hash` 用於去重。

### 「微調樣本」可改什麼

1. **座標**：拖曳或輸入管線折線點，伺服器重跑 rules，立刻顯示剩餘違規。
2. **修正方案**：改 solver 給的答案（例如改成繞道而非上移），重新驗證。
3. **推理文字（`<think>` 與理由）**：可直接編輯；若座標被改，系統提示「文字可能已過期」，並提供「依新答案重新產生」按鈕。
4. 人工修改過的版本標記 `source = human`，匯出時可選擇是否包含。

### API（草案）

```
GET  /api/samples?status=pending&difficulty=2
GET  /api/samples/{id}                     最新版本 + 違規 + 修正結果
POST /api/samples/{id}/validate            body: scene/fixed → 回傳違規列表（不存檔）
POST /api/samples/{id}/versions            存新版本（需通過 validate 或帶 override）
POST /api/samples/{id}/review              {decision, reason}
POST /api/generate                         {n, seed, difficulty} 產生新的待審樣本
GET  /api/export?format=jsonl              只匯出 approved
```

## 4. 階段計畫

### Phase 0：骨架與契約
- 專案結構、pyproject、pytest、ruff。
- `scene.py` dataclass 與 JSON schema（含 `schema_version`）。
- 驗收：scene 寫出再讀回完全一致。

### Phase 1：最小可跑（SPEC §9）
- `geometry.py`：線段-線段最小距離、線段-長方體相交。
- 固定兩條會碰撞的管、基本 rules、上移修正。
- Plotly 輸出單一 HTML：修正前後並排，碰撞處標紅，可旋轉。
- 驗收：瀏覽器開啟後能旋轉並看出前後差異。

### Phase 2：規則與 Solver
- rules：管-管碰撞（含間隙）、穿牆（開口例外）、淨高、坡度。
- solver：系統優先級（排水 > 風管 > 水管 > 電管）、垂直位移 → 水平位移 → dogleg 繞道，每步驗證。
- 每條規則有手算過的測試，含邊界值。
- 驗收：修完零違規；無解場景被丟棄並記錄原因。

### Phase 3：生成器
- 先造合法場景，再用 mutator 注入問題；含 10–15% 無問題場景。
- 難度 1–4：管數、系統數、問題數、樓層數。
- 驗收：同 seed byte 一致；各難度解出率有統計。

### Phase 4：組裝器與 JSONL
- 由 solver trace 產生 `<think>`，多組措辭模板。
- 最終答案附可解析 JSON。
- 任務變體：只列碰撞、直接修正、附理由。
- 驗收：LLaMA-Factory 可讀；抽樣人工檢查 20 筆。

### Phase 5：Server 與審核介面
- FastAPI + SQLite，實作第 3 節的資料表與 API。
- 審核頁：待審清單、場景檢視、違規列表、核准 / 退回 / 編輯。
- 編輯：輸入座標 → `validate` → 顯示即時違規；存成新版本。
- 匯出：只輸出 approved，附統計（各難度、各問題類型數量）。
- 驗收：從產生到核准到匯出 JSONL 全程在網頁完成；修改後未通過規則的樣本無法核准。

### Phase 6：看圖學習功能
- 見第 6 節。可與 Phase 5 並行，先做最有用的幾項。

### Phase 7：評測腳本
- 解析模型輸出 → rules → 通過率與分項成績。
- 之後可作為 RL 的 reward。

### Phase 8：Three.js 檢視器
- 取代 Plotly：真正圓管、半透明牆、OrbitControls、前後切換滑桿。
- 可選：IFC 輸出（ifcopenshell）。
- 驗收：同一組 API 與 JSON，不需改後端。

## 5. 檢視器共同需求

- 系統用固定顏色區分（例如排水、風管、給水、電管各一色），圖例常駐。
- 牆半透明，管線可穿透看到。
- 違規位置以紅色標記，點擊顯示：涉及的元件、規則名稱、實測數值與門檻。
- 修正前 / 後可切換或並排；被移動的管線高亮。
- 支援三種視角：3D、平面圖（俯視）、剖面圖（側視）。

## 6. 看圖學習功能

目標：讓使用者邊審核邊學會讀 MEP 圖面。

1. **圖例與名詞表**：側欄說明各系統、管徑標示、標高（EL）、淨高、保溫層、套管、開口等詞，滑鼠移到物件時對應名詞高亮。
2. **平面圖 + 剖面圖並列**：同一個碰撞，在俯視圖看「位置」，在剖面圖看「高度關係」，並用尺寸線標出間距。這是 MEP 協調圖最常見的讀法。
3. **「為什麼這是問題？」面板**：點擊違規後，用白話顯示「管 A 管底 2.31 m，低於需求 2.40 m，差 0.09 m」，並畫出該數值對應的尺寸線。
4. **逐步導覽模式**：依 solver trace 一步步播放（偵測 → 選擇移動哪條 → 移動 → 驗證），每步高亮相關物件。同一份 trace 也是 `<think>` 的來源，所以看圖和看訓練文字會互相印證。
5. **測驗模式（可選）**：先隱藏違規，請使用者自己指出問題，再揭曉答案。

## 7. 風險

| 風險 | 對策 |
|---|---|
| Solver 解不出複雜場景 | 丟棄並記錄，追蹤丟棄率，不產出壞樣本 |
| 模板推理太單調，模型背模板 | 多組措辭、隨機化推理順序、人工抽查 |
| 浮點誤差破壞可重現 | 統一規整與固定 round |
| 人工修改後文字與答案不一致 | 座標變動時標記文字過期，提供重新產生 |
| SQLite 並行寫入 | 先單人使用；之後有多人審核再換 Postgres |
| 審核量大 | 提供篩選（難度、問題類型）與批次核准（僅限規則全過者） |

## 8. 已確認的決定

- 審核者只有使用者一人，**不做登入**（日後需要再加；`reviews.reviewer` 欄位先保留、預設 `owner`）。
- `source = human` 的樣本**預設納入匯出**，並在每筆輸出帶 `meta.source = "human"` 標記，方便之後分析或排除。

## 8.1 實作時的範圍調整

- **儲存與核准分開把關**：存新版本不要求通過規則（可當草稿，狀態回到 pending）；**核准**才檢查「assistant 文字內的 json 答案」套用後零違規、`<think>` 結構正確、文字與修正一致。未通過時需 override 並填寫原因。
- 編輯已核准的樣本會撤銷核准（內容已變）。

- **多樓層暫不實作**：難度分級目前以管數、系統數、問題數、牆開口、兩端固定管、天花板高度與間隙為主；樓層需要樓板與逐層淨高規則，列為後續擴充。

## 8.2 沿用預設（未被反對）

- 淨高採第 1 節定義（管底標高 ≥ 2.4 m 且管頂 ≤ 天花板）。
- 單位公尺、Z 向上、提示語言繁體中文。

## 9. 建議的第一步

Phase 0 與 Phase 1。完成後即可在瀏覽器看到第一個可旋轉的場景，再往 Phase 5 的 Server 版接。
