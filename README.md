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

## 本地开发（不用 Docker）

```bash
pip install -r requirements-dev.txt
uvicorn app.main:app --reload   # 服务运行在 http://localhost:8000
python -m pytest                # 运行测试（含与暴力枚举对照的随机化测试）
```

## 项目结构

```
app/
  main.py      # FastAPI 应用：/assign、/health、自动 OpenAPI
  models.py    # 请求校验与响应模型（Pydantic）
  solver.py    # 基于 NetworkX 最大流/最小费用流的求解器
tests/
  test_api.py  # 单元测试 + 暴力枚举对照的随机化测试
Dockerfile
docker-compose.yml
requirements.txt        # 运行时依赖
requirements-dev.txt    # 额外测试依赖
```
