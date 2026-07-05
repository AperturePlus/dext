# 后端导师推荐联调测试报告

## 1. 测试概况

本次测试目标是验证后端导师推荐链路是否能在当前真实环境中跑通，并结合真实 catalog SQLite 与 Qdrant 数据核对推荐结果是否一致、可解释、匹配用户意图。

- 测试时间：2026-07-04
- 后端地址：`http://127.0.0.1:21540/api/v1`
- catalog SQLite：`D:\pyprj\dext\data\catalog\catalog.db`
- Qdrant alias：`dext_professors_current`
- ACTIVE build：`019f2627-37a4-717b-8830-96d2c9e369a0`
- 推荐接口：`POST /api/v1/recommendations/mentors`
- 导师详情接口：`GET /api/v1/professors/{professor_id}`
- 身份接口：`POST /api/v1/identity/anonymous`

本次测试未修改代码，未写入 catalog、Qdrant 或 Neo4j。唯一写入操作是创建匿名 app-state identity，用于获取推荐接口所需的 bearer token。

## 2. 测试请求

使用匿名身份 token 调用推荐接口，请求体如下：

```json
{
  "prompt": "我是计算机科学本科生，想申请硕士，研究兴趣是计算机视觉、医学影像和多模态学习，最好在北京或上海，帮我推荐合适导师。",
  "profile": {
    "degree_stage": "本科",
    "target_degree": "硕士",
    "school": "某211高校",
    "major": "计算机科学与技术",
    "research_interests": [
      "计算机视觉",
      "医学影像",
      "多模态学习"
    ],
    "highlights": "做过医学影像分割课程项目，熟悉 PyTorch。",
    "score": {
      "gpa": 3.7,
      "scale": 4.0,
      "rank_mode": "none"
    }
  },
  "limit": 5
}
```

## 3. 接口执行结果

### 3.1 匿名身份

- 接口：`POST /api/v1/identity/anonymous`
- HTTP 状态：`200`
- 耗时：约 `0.06s`
- 结果：成功返回 bearer token，后续推荐请求使用该 token。

### 3.2 导师推荐

- 接口：`POST /api/v1/recommendations/mentors`
- HTTP 状态：`200`
- 耗时：约 `39.88s`
- 返回 `build_id`：`019f2627-37a4-717b-8830-96d2c9e369a0`
- `ranking_profile_version`：`ranking-v1`
- `generation_profile_version`：`generation-v1`

后端正确识别了用户意图：

```json
{
  "degree_stage": "硕士",
  "preferred_locations": [
    "北京",
    "上海"
  ],
  "preferred_universities": [],
  "research_interests": [
    "计算机视觉",
    "医学影像",
    "多模态学习"
  ],
  "uncertainties": []
}
```

推荐接口返回 warnings：

```json
[
  {
    "code": "weak_explanation",
    "message": "one or more results lack traceable evidence",
    "severity": "warning"
  },
  {
    "code": "details_unavailable",
    "message": "50 detail(s) unavailable; degraded",
    "severity": "warning"
  }
]
```

这两个 warning 与后续观察到的推荐卡片解释不足、详情补全降级一致。

## 4. 推荐结果

| 排名 | 导师 | 学校 | 学院 | 职称 | 匹配分 | 匹配级别 |
|---:|---|---|---|---|---:|---|
| 1 | 肖阳 | 华中科技大学 | 人工智能与自动化学院 | 教授 | 0.58 | 中 |
| 2 | 郑清萍 | 厦门大学 | 信息学院 | unknown | 0.41 | 低 |
| 3 | 王璐 | 东北大学 | 信息科学与工程学院 | 副教授 | 0.37 | 低 |
| 4 | 胡涛 | 华中科技大学 | 软件学院 | 教授 | 0.28 | 低 |
| 5 | 白翔 | 华中科技大学 | 软件学院 | 教授 | 0.28 | 低 |

推荐卡片存在共同问题：

- 5 条 `research_fields` 均为空。
- 5 条 `reason` 基本都是同一句：`综合匹配信号较高；当前缺少可回溯详情证据`。
- 用户地域偏好为北京或上海，但 5 位导师分别来自武汉、厦门、沈阳、武汉、武汉。

## 5. 导师详情复测

首次批量获取详情时，肖阳与郑清萍详情接口返回过 `500 internal_error`：

- 肖阳 request_id：`f6d65fc6-485a-4b0f-9560-c47ebe4c8358`
- 郑清萍 request_id：`23ba10e6-b8bd-41a5-b14a-aa8c1204d423`

随后单独复测，5 位导师详情均返回 `200`。这说明详情接口存在瞬时不稳定或并发/超时抖动，但不是数据本身缺失。

| 导师 | 详情状态 | 详情摘要 |
|---|---:|---|
| 肖阳 | 200 | 详情 bio 明确包含计算机视觉、机器学习、模式识别、多模态大语言模型。 |
| 郑清萍 | 200 | 详情显示招硕士生，研究字段为空间智能、具身智能、生成式人工智能。 |
| 王璐 | 200 | 详情 bio 包含医学图像处理经历，DB 研究方向包含医学影像、多模态。 |
| 胡涛 | 200 | 详情包含计算机视觉与人工智能、多模态学习、扩散模型。 |
| 白翔 | 200 | 详情包含计算机视觉与模式识别。 |

## 6. 真实数据库核对

### 6.1 catalog ACTIVE build

catalog 中当前 ACTIVE build 为：

```json
{
  "id": "019f2627-37a4-717b-8830-96d2c9e369a0",
  "status": "ACTIVE",
  "embedding_provider": "siliconflow",
  "embedding_model": "BAAI/bge-m3",
  "embedding_fingerprint": "f0dd7ab3f1d75837c3d8fd3419109ee5d563254ee7d2aa6d45e0212fb8b9fd52",
  "embedding_dimension": 1024,
  "taxonomy_version": "research-topics-v1",
  "finished_at": "2026-07-04T08:04:33.568543+00:00"
}
```

推荐响应 `build_id` 与 catalog ACTIVE build 一致。

### 6.2 Qdrant alias

Qdrant alias 核对结果：

```json
{
  "alias_name": "dext_professors_current",
  "collection_name": "dext_professors__019f2627-37a4-717b-8830-96d2c9e369a0"
}
```

Qdrant collection 与推荐响应 build 对齐。

### 6.3 推荐导师 DB 状态

5 位推荐导师均满足：

- 存在于 `canonical_professors`
- `active=1`
- `role_status=included`
- Qdrant payload 中 `build_id` 与推荐 build 一致
- Qdrant payload 中 `profile_hash` 与 catalog `professor_profiles.profile_hash` 一致
- Qdrant payload 中 `embedding_fingerprint` 与 ACTIVE build 一致

底层数据一致性通过。

## 7. 逐导师匹配分析

### 7.1 肖阳

- 学校：华中科技大学
- 学院：人工智能与自动化学院
- 职称：教授
- DB 研究方向：
  - 面向无人艇与无人机应用的目标检测识别与跟踪
  - 面向智能医护的人体跌倒与坠床实时检测
  - 非受限场景条件下的人体实时眨眼检测
  - 三维人体与手部姿态估计与关节点提取
  - 基于多模感知信息的人体行为分析与理解
- 详情 bio 支撑：
  - 计算机视觉
  - 机器学习
  - 模式识别
  - 多模态大语言模型

判断：相关。该导师与计算机视觉、多模态方向匹配较好。但所在城市为武汉，不满足北京/上海地域偏好。

### 7.2 郑清萍

- 学校：厦门大学
- 学院：信息学院
- 职称：unknown
- DB 研究方向：
  - 生成式人工智能(AIGC)
  - 空间智能
  - 具身智能
- 详情 bio：招硕士生

判断：偏弱。该导师与 AI 大方向有关，但与用户明确提出的计算机视觉、医学影像、多模态学习的证据不足。所在城市为厦门，不满足北京/上海地域偏好。

### 7.3 王璐

- 学校：东北大学
- 学院：信息科学与工程学院
- 职称：副教授
- DB 研究方向：
  - 医学图文跨模态融合与决策支持
  - 医学图像多模态分析与智能诊断
  - 医学影像重建算法创新与优化
- 详情 bio 支撑：
  - 香港大学医学图像处理博士经历
  - 医学影像相关研究经历

判断：相关。与医学影像、多模态方向匹配较好。但所在城市为沈阳，不满足北京/上海地域偏好。另一个问题是详情 `research_fields` 只返回了 `优化`，没有展示 DB 中更关键的医学影像/多模态方向。

### 7.4 胡涛

- 学校：华中科技大学
- 学院：软件学院
- 职称：教授
- DB 研究方向：
  - 计算机视觉与人工智能
  - 高效视觉表征学习
  - 多模态学习
  - 流匹配(Flow Matching)
  - 扩散模型(Diffusion Models)
  - 生成式模型
- 详情 bio 支撑：
  - 计算机视觉
  - 人工智能
  - 基础模型
  - CVPR、ICCV、ECCV 等视觉领域成果

判断：强相关。该导师与计算机视觉、多模态学习高度匹配。问题是所在地为武汉，不满足北京/上海地域偏好。

### 7.5 白翔

- 学校：华中科技大学
- 学院：软件学院
- 职称：教授
- DB 研究方向：
  - 计算机视觉与模式识别
- 详情 bio 支撑：
  - 计算机视觉
  - 模式识别
  - 机器视觉与智能系统

判断：相关。该导师与计算机视觉方向匹配，但不涉及医学影像或多模态。所在地为武汉，不满足北京/上海地域偏好。

## 8. 问题清单

### P1：地域偏好识别了但没有落实

后端 `query_understanding` 已识别 `preferred_locations=["北京", "上海"]`，但最终 5 个推荐结果没有任何北京或上海导师。

影响：

- 用户明确指定地域偏好时，推荐结果体验明显不符合预期。
- 如果当前系统将地域作为软条件，也应在推荐理由或 limitations 中解释为什么放宽条件。

建议：

- 检查 location preference 在 ranking/filter pipeline 中是否生效。
- 如果是软约束，给北京/上海候选明显加权。
- 如果没有满足地域条件的高相关导师，应在结果中说明“已放宽地域条件”。

### P1：推荐解释降级，卡片不可解释

所有推荐卡片的 `research_fields` 为空，`reason` 基本是模板化降级文案：

```text
综合匹配信号较高；当前缺少可回溯详情证据
```

影响：

- 前端卡片无法展示为什么推荐该导师。
- 即使 DB 中有充分证据，例如胡涛、白翔、王璐，推荐卡片仍没有把证据带出来。

建议：

- 优先排查 detail hydration、fact hydration、explanation generation。
- 推荐卡片至少应回填 `research_areas_text`、approved topics 或 selected research statements。
- 对 `weak_explanation` 增加可观测日志，定位是哪一步没有拿到证据。

### P1：推荐流程内部详情补全降级

推荐响应包含：

```text
details_unavailable: 50 detail(s) unavailable; degraded
```

但后续对同一批导师单独调用详情接口可以返回 200。这说明推荐流程内部获取详情时可能存在 timeout、并发抖动、批量 hydration 限制或异常吞掉。

影响：

- 导致推荐卡片字段空、理由模板化。
- 使推荐质量不稳定。

建议：

- 检查推荐流程内部对详情的批量获取方式、超时、并发和异常处理。
- 对 `details_unavailable` 记录具体 entity_id、异常类型、耗时。
- 区分数据确实缺失与 transient failure。

### P2：详情接口存在瞬时 500

首次批量获取详情时，肖阳、郑清萍返回 `500 internal_error`；复测后同一接口同一 ID 返回 200。

影响：

- 用户打开导师详情时可能偶发失败。
- 可能与 catalog 读取超时、并发访问、连接池或上游 readiness 状态有关。

建议：

- 根据 request_id 查服务端日志：
  - `f6d65fc6-485a-4b0f-9560-c47ebe4c8358`
  - `23ba10e6-b8bd-41a5-b14a-aa8c1204d423`
- 给详情接口补充更具体的错误分类，避免全部变成 generic `internal_error`。

### P2：详情字段 topic 映射不充分

王璐 DB 研究方向明确包含医学影像和多模态，但详情 `research_fields` 只返回 `优化`。

影响：

- 详情页展示弱化了最关键的匹配证据。
- 推荐解释可能也因此拿不到强匹配字段。

建议：

- 检查 `approved_topics` 与 `research_statements` 的选择优先级。
- 当 approved topic 太少或过泛时，应回退展示原始 research statements。

## 9. 总体结论

后端推荐链路整体可用，接口能够完成匿名认证、推荐、详情获取，并且推荐结果与 catalog/Qdrant 的底层数据一致性通过。

推荐准确性中等：5 个结果中，肖阳、王璐、胡涛、白翔与用户研究方向有明确关系，郑清萍匹配偏弱。胡涛与王璐尤其符合“计算机视觉/医学影像/多模态”方向。

主要问题不在底层数据一致性，而在推荐质量层：

- 地域偏好未落实。
- 推荐卡片缺少研究字段和证据解释。
- 推荐内部详情补全降级。
- 详情接口存在瞬时 500。
- topic/field 展示没有充分反映 DB 中的强匹配研究方向。

建议优先修复推荐解释和详情补全链路，其次修复地域偏好排序或过滤策略。
