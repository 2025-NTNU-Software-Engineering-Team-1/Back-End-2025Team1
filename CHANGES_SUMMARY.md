# 修改摘要 - Problem API Schema 更新

## 修改日期
2025-11-08

## 目標
讓 MongoEngine 的 Problem schema 能接受 API 傳入的新欄位，同時保持與現有 API 的兼容性。

## 修改檔案

### 1. `mongo/engine.py`
**修改內容：**
- 在 `Problem` Document 中添加新欄位：
  - `input_description` (StringField, db_field='inputDescription')
  - `output_description` (StringField, db_field='outputDescription')
  - `hint` (StringField)
  - `sample_input` (ListField, db_field='sampleInput')
  - `sample_output` (ListField, db_field='sampleOutput')
  - `config` (DictField, null=True)

**影響：**
- 這些欄位與現有的 `ProblemDescription` 嵌入文檔並存
- 允許 API 直接設置這些欄位，同時保持數據庫兼容性

### 2. `mongo/problem/problem.py`
**修改內容：**
- 移除了重複的欄位定義（這些欄位現在在 engine.Problem 中定義）
- 修改 `Problem.add()`:
  - 創建完整的 `ProblemDescription` 對象
  - 同時設置獨立的欄位
- 修改 `Problem.edit_problem()`:
  - 處理 description 字典並創建 `ProblemDescription` 對象
  - 同時更新獨立的欄位

**影響：**
- 確保數據在嵌入文檔和獨立欄位中都有保存
- 維持向後兼容性

### 3. `model/utils/request.py`
**修改內容：**
- 修復 `Request.json` 裝飾器的類型檢查邏輯
- 允許 `None` 值通過類型檢查（對於可選參數）
- 修改條件：`if v is None or t is None or type(v) is t`

**影響：**
- 修復了當可選參數未提供時的 ValueError
- 允許帶類型標註的欄位（如 `config: dict`）可以為 None

### 4. `model/problem.py`
**修改內容：**
- 移除 `create_problem` 裝飾器中重複的 `type` 參數

**影響：**
- 修復了參數重複導致的問題

### 5. `tests/test_problem.py`
**修改內容：**
- 在 `test_add_online_problem` 中添加課程創建

**影響：**
- 測試現在可以正常運行

## 測試結果

### 通過的測試
✅ `tests/test_problem.py::TestProblem::test_add_online_problem`
✅ `tests/test_problem.py` - 14/15 測試通過

### 已知問題
- 部分測試失敗是由於測試數據準備問題（用戶/課程未創建），不是本次修改造成的
- `engine.Task` 不存在的問題（應使用 `engine.ProblemCase`）在某些測試中出現

## 驗證方式

```bash
# 運行特定測試
.venv.bak/bin/pytest tests/test_problem.py::TestProblem::test_add_online_problem -xvs

# 運行所有 problem 測試
.venv.bak/bin/pytest tests/test_problem.py -x --tb=no -q
```

## 兼容性說明

- ✅ 現有 API 端點保持不變
- ✅ 數據庫 schema 向後兼容
- ✅ 新增欄位都有預設值，不影響現有數據
- ✅ `ProblemDescription` 嵌入文檔仍然存在並正常工作
