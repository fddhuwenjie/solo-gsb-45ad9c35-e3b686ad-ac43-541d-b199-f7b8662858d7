# 同行评审分派 API

基于 **Python + FastAPI + NetworkX** 的本地同行评审分派服务。输入稿件与评审人信息，
输出满足全部约束的最优分派；无解时返回最小容量缺口证明。

## 规则说明

- **匹配分**：某 (稿件, 评审人) 组合的匹配分 = 评审人对该稿件**各主题评分之和**。
- **可用组合**：评审人与稿件无冲突，且匹配分 **> 0**；冲突或总分为 0 的组合不可用。
- **约束**：
  - 每篇稿件恰好分派 `required_reviewers` 名**不同**的评审人（同一稿件不得重复使用同一评审人）；
  - 每名评审人被指派的稿件总数不超过其 `capacity`。
- **优化顺序**（完整分派存在时）：
  1. 最小化评审人的**最大负载率**（负载率 = 已指派数 / 容量）；
  2. 在此基础上最大化**总匹配分**；
  3. 仍并列时，按 (稿件 ID, 评审人 ID) 排序后的分派列表取**字典序最小**者
     （ID 按字符串字典序比较）。
- **无解证明**：当不存在完整分派时，返回一个稿件子集 `S` 及其全部可用评审人 `N(S)`，
  满足 `S` 的需求总数 > `N(S)` 的有效容量和（由最大流最小割保证存在）。由于同一评审人
  对同一稿件只能计一次，评审人对着子集的**有效容量** = `min(容量, 子集中其可评审的稿件数)`。
  在所有违反该条件的子集中，先取**稿件数最少**者，再按稿件 ID 字典序取最小者。

## 快速开始（Docker Compose 一条命令）

```bash
docker compose up --build
```

服务启动后访问：

| 地址 | 说明 |
| --- | --- |
| `http://localhost:8000/assign` | `POST` 求解分派 |
| `http://localhost:8000/reassign` | `POST` 评审人退出后的应急改派 |
| `http://localhost:8000/health` | `GET` 健康检查 |
| `http://localhost:8000/docs` | Swagger UI 交互文档 |
| `http://localhost:8000/openapi.json` | OpenAPI 规范 |

停止：`docker compose down`。

## 请求格式 `POST /assign`

```json
{
  "manuscripts": [
    {"id": "m1", "topics": ["nlp", "ml"], "required_reviewers": 2}
  ],
  "reviewers": [
    {"id": "r1", "scores": {"nlp": 5, "ml": 4}, "capacity": 2, "conflicts": ["m2"]}
  ]
}
```

- `manuscripts`：1～10 篇，`id` 唯一；`topics` 至少一个且不重复；`required_reviewers ≥ 1`。
- `reviewers`：1～20 名，`id` 唯一；`scores` 为 主题 → 非负评分（缺省主题按 0 计）；
  `capacity ≥ 0`；`conflicts` 为冲突稿件 ID 列表（必须引用存在的稿件）。
- 不合法的请求体会返回 `422` 及具体错误信息。

## 示例

### 可行实例

```bash
curl -s -X POST http://localhost:8000/assign \
  -H 'Content-Type: application/json' \
  -d '{
    "manuscripts": [
      {"id": "m1", "topics": ["nlp", "ml"], "required_reviewers": 2},
      {"id": "m2", "topics": ["vision"], "required_reviewers": 1}
    ],
    "reviewers": [
      {"id": "r1", "scores": {"nlp": 5, "ml": 4}, "capacity": 2, "conflicts": ["m2"]},
      {"id": "r2", "scores": {"nlp": 3, "vision": 5}, "capacity": 1, "conflicts": []},
      {"id": "r3", "scores": {"ml": 4, "vision": 2}, "capacity": 1, "conflicts": []}
    ]
  }'
```

响应（`200 OK`）：

```json
{
  "status": "feasible",
  "assignments": [
    {"manuscript_id": "m1", "reviewer_id": "r1", "score": 9.0},
    {"manuscript_id": "m1", "reviewer_id": "r3", "score": 4.0},
    {"manuscript_id": "m2", "reviewer_id": "r2", "score": 5.0}
  ],
  "total_score": 18.0,
  "max_load_ratio": 1.0,
  "loads": {"r1": 1, "r2": 1, "r3": 1}
}
```

### 无解实例（返回最小容量缺口证明）

```bash
curl -s -X POST http://localhost:8000/assign \
  -H 'Content-Type: application/json' \
  -d '{
    "manuscripts": [
      {"id": "m1", "topics": ["nlp"], "required_reviewers": 2},
      {"id": "m2", "topics": ["nlp"], "required_reviewers": 2}
    ],
    "reviewers": [
      {"id": "r1", "scores": {"nlp": 5}, "capacity": 2, "conflicts": []},
      {"id": "r2", "scores": {"nlp": 4}, "capacity": 1, "conflicts": []},
      {"id": "r3", "scores": {"nlp": 3}, "capacity": 1, "conflicts": ["m1", "m2"]}
    ]
  }'
```

响应（`200 OK`）：`{m1, m2}` 共需 4 个评审位置，但可用评审人只有 r1、r2
（r3 与两篇稿件均冲突），有效容量和为 `min(2,2) + min(1,2) = 3`，缺口为 1。

```json
{
  "status": "infeasible",
  "proof": {
    "manuscripts": ["m1", "m2"],
    "reviewers": ["r1", "r2"],
    "demand": 4,
    "capacity": 3,
    "gap": 1
  }
}
```

## 应急改派 `POST /reassign`

评审人退出后，在**尽量少改动原分派**的前提下补齐缺口。请求体在 `/assign` 的
`manuscripts`、`reviewers` 基础上增加三个字段：

- `original_assignments`：**原完整分派**的全部 `(稿件, 评审人)` 关系。
  服务端会重新核验：每篇稿件恰好得到所需数量、不重复、不违反冲突、匹配分为正、
  每人不超过容量；任一不满足返回 `422`。
- `withdrawn_reviewer_id`：退出者 ID（必须是已知评审人）。其在原分派中的**全部**
  关系一律撤销。
- `confirmed_assignments`（可省略）：原分派中**已经确认**的关系。每条都必须属于
  `original_assignments`，否则 `422`。非退出者的确认关系在改派中**强制保留**；
  退出者的确认关系随其一并撤销。

改派为完整分派（同样遵守需求、冲突、容量约束），优化顺序为：

1. **最大化保留**其余原分派关系的数量（改动最少）；
2. 在保留数相同的完整改派中，最小化评审人**最大负载率**（退出者不参与）；
3. 再最大化**总匹配分**；
4. 仍并列时，按 (稿件 ID, 评审人 ID) 排序后的关系列表取**字典序最小**者。

不存在完整改派时返回 `status: "infeasible"` 与残量问题（扣除已确认关系、移除退出者
之后）的最小容量缺口证明，**不会输出任何部分分派**。

### 改派请求示例

```bash
curl -s -X POST http://localhost:8000/reassign \
  -H 'Content-Type: application/json' \
  -d '{
    "manuscripts": [
      {"id": "m1", "topics": ["nlp", "ml"], "required_reviewers": 2},
      {"id": "m2", "topics": ["vision"], "required_reviewers": 1}
    ],
    "reviewers": [
      {"id": "r1", "scores": {"nlp": 5, "ml": 4}, "capacity": 2, "conflicts": ["m2"]},
      {"id": "r2", "scores": {"nlp": 3, "vision": 5}, "capacity": 1, "conflicts": []},
      {"id": "r3", "scores": {"ml": 4, "vision": 2}, "capacity": 2, "conflicts": []}
    ],
    "original_assignments": [
      {"manuscript_id": "m1", "reviewer_id": "r1"},
      {"manuscript_id": "m1", "reviewer_id": "r3"},
      {"manuscript_id": "m2", "reviewer_id": "r2"}
    ],
    "withdrawn_reviewer_id": "r2",
    "confirmed_assignments": [
      {"manuscript_id": "m1", "reviewer_id": "r1"}
    ]
  }'
```

响应（`200 OK`）：`r2` 退出，原关系 `(m2, r2)` 撤销（`revoked_assignments`）；
已确认的 `(m1, r1)` 保留；原关系 `(m1, r3)` 也保留；唯一新增关系是 `(m2, r3)`
（`added_assignments`）。`loads` 给出改派后**每名评审人**（含退出者，负载为 0）
的指派数。

```json
{
  "status": "feasible",
  "assignments": [
    {"manuscript_id": "m1", "reviewer_id": "r1", "score": 9.0},
    {"manuscript_id": "m1", "reviewer_id": "r3", "score": 4.0},
    {"manuscript_id": "m2", "reviewer_id": "r3", "score": 2.0}
  ],
  "revoked_assignments": [
    {"manuscript_id": "m2", "reviewer_id": "r2", "score": 5.0}
  ],
  "added_assignments": [
    {"manuscript_id": "m2", "reviewer_id": "r3", "score": 2.0}
  ],
  "withdrawn_reviewer_id": "r2",
  "total_score": 15.0,
  "max_load_ratio": 1.0,
  "loads": {"r1": 1, "r2": 0, "r3": 2}
}
```

### 改派无解示例

其他条件不变，但 `r3` 容量只有 1：扣除已确认的 `(m1, r1)` 后，残量需求为 2
（m1 还差 1、m2 差 1），唯一可用评审人 `r3` 对残量问题的有效容量只有 1，
返回缺口证明而非部分分派：

```json
{
  "status": "infeasible",
  "proof": {
    "manuscripts": ["m1", "m2"],
    "reviewers": ["r3"],
    "demand": 2,
    "capacity": 1,
    "gap": 1
  }
}
```

### 非法输入

下列情况返回 `422`，并在错误信息中指出具体原因，例如：

```bash
# confirmed_assignments 中的关系不属于原分派
curl -s -X POST http://localhost:8000/reassign \
  -H 'Content-Type: application/json' \
  -d '{
    "manuscripts": [
      {"id": "m1", "topics": ["t"], "required_reviewers": 1},
      {"id": "m2", "topics": ["t"], "required_reviewers": 1}
    ],
    "reviewers": [
      {"id": "r1", "scores": {"t": 5}, "capacity": 2, "conflicts": []},
      {"id": "r2", "scores": {"t": 4}, "capacity": 2, "conflicts": []}
    ],
    "original_assignments": [
      {"manuscript_id": "m1", "reviewer_id": "r1"},
      {"manuscript_id": "m2", "reviewer_id": "r2"}
    ],
    "withdrawn_reviewer_id": "r1",
    "confirmed_assignments": [
      {"manuscript_id": "m1", "reviewer_id": "r2"}
    ]
  }'
# 422: confirmed_assignments 中的关系 (m1, r2) 不属于原分派
```

其他典型非法输入：`withdrawn_reviewer_id` 不是已知评审人；原分派稿件数量不符
（不完整或多余）、关系重复、引用未知 ID、违反冲突、匹配分为 0、超过容量；
`confirmed_assignments` 关系重复等。

### 健康检查

```bash
curl -s http://localhost:8000/health
# {"status":"ok"}
```

## 实现要点

- 分派建模为二分图 b-匹配：源点 → 稿件（容量 = 所需评审数）→ 评审人（容量 = 1，
  保证同一稿件不重复使用同一评审人）→ 汇点（容量 = 评审人容量/负载率上限）。
- 用 `networkx.maximum_flow` 判定可行性，并在候选负载率上二分搜索最小可行值
  （`Fraction` 精确取整，避免浮点误差）。
- 用 `networkx.network_simplex` 求最小费用流，边权为
  `-(匹配分 × 2^M) - 2^(M-1-名次)`：匹配分项占主导，指数权重使费用最小等价于
  字典序最小，一次求解同时满足三级优化目标。
- 无解时枚举稿件子集（按子集大小、ID 字典序），找到第一个需求超过有效容量和的
  子集即为最小容量缺口证明。
- `/reassign` 先校验原分派的完整性与全部约束，再把「非退出者的已确认关系」作为
  预流扣除（稿件剩余需求、评审人剩余容量、已占用边），退出者的边全部移除，得到
  残量二分图。随后三级求解：最小费用流（保留原关系边权为 -1）求最大保留数；
  在负载率候选上二分求最小可行最大负载率；最后用一条最小费用流按
  `-(保留权) -(匹配分 × 2^M) - 2^(M-1-名次)` 同时实现保留数优先、总分最大、
  字典序最小。残量问题无解时复用同一套容量缺口证明。

## 本地开发（不用 Docker）

```bash
pip install -r requirements-dev.txt
uvicorn app.main:app --reload   # 服务运行在 http://localhost:8000
python -m pytest                # 运行测试（含与暴力枚举对照的随机化测试）
```

## 项目结构

```
app/
  main.py      # FastAPI 应用：/assign、/reassign、/health、自动 OpenAPI
  models.py    # 请求校验与响应模型（Pydantic）
  solver.py    # 基于 NetworkX 最大流/最小费用流的求解器与应急改派
tests/
  test_api.py       # /assign 单元测试 + 暴力枚举对照的随机化测试
  test_reassign.py  # /reassign 单元测试 + 暴力枚举对照的随机化测试
Dockerfile
docker-compose.yml
requirements.txt        # 运行时依赖
requirements-dev.txt    # 额外测试依赖
```
