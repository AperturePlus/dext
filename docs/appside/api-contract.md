# SchoNavi API Contract

This contract is the source of truth for future backend development. Flutter
domain entities and repository interfaces are the upstream model; the backend
must return these shapes through `/api/v1` endpoints.

## Conventions

- Base path: `/api/v1`
- Flutter `API_BASE_URL` is the backend origin, for example
  `https://api.example.com`; do not include `/api/v1` because the client adds
  that path.
- Content type: `application/json; charset=utf-8`
- Field names: `snake_case`
- Time fields: ISO-8601 strings
- Every first-party request sends `X-Request-ID: <uuid-v7>`. The backend must
  echo the same value in the `X-Request-ID` response header for successful,
  business-error, and HTTP-error responses so client diagnostics can be
  correlated with backend logs. Browser deployments must include
  `X-Request-ID` in `Access-Control-Expose-Headers`.
- Success envelope:

```json
{ "code": 0, "message": "ok", "data": {} }
```

- Error envelope:

```json
{ "code": 40001, "message": "输入内容不合法", "data": null }
```

- Paginated list:

```json
{ "items": [], "page": 1, "page_size": 20, "total": 0 }
```

Common error codes:

| Code | HTTP | Meaning |
|---:|---:|---|
| `40001` | 400 | Invalid request |
| `40101` | 401 | Unauthorized |
| `40301` | 403 | Forbidden |
| `40401` | 404 | Resource not found |
| `42201` | 422 | Validation failed |
| `42901` | 429 | Rate limited |
| `50001` | 500 | Server error |

### Identity and ownership

- The first anonymous request calls `POST /identity/anonymous`.
- Mobile clients persist the returned bearer token in secure storage and send
  `Authorization: Bearer <token>`.
- Same-origin Web clients use the `HttpOnly`, `Secure`, `SameSite=Lax`
  `scho_anonymous` cookie set by the same endpoint.
- Chat ownership always comes from the authenticated identity. An `owner_id`
  supplied by a request body is ignored/rejected; it is never an authorization
  source.
- Missing or invalid identity returns `401`. A session owned by another
  identity is not exposed and returns `404`.
- Except for public read endpoints such as `/home/prompts` and
  `/professors/{professor_id}`, endpoints that carry user input, profile data,
  favorites, history, or preparation-plan snapshots require bearer or cookie
  identity.

### Conversation identifiers and concurrency

`session_id`, `turn_id`, `message_id`, `attempt_id`, and `request_id` are
independent UUIDv7 values. Their strings carry no parent/child semantics.
Conversation mutations use `expected_revision`; a mismatch fails rather than
silently overwriting newer state. Creating a turn or attempt also requires
`Idempotency-Key: <request_id>`, and the header must equal the JSON
`request_id`. For those mutations, `X-Request-ID`, `Idempotency-Key`, and the
JSON `request_id` must carry the same UUIDv7 value.

## Shared Models

### UserProfile

```json
{
  "name": "王同学",
  "gender": "undisclosed",
  "degree_stage": "本科",
  "target_degree": "申请硕士",
  "school": "四川大学",
  "major": "计算机科学与技术",
  "research_interests": ["计算机视觉", "医学影像"],
  "highlights": "有一段医学影像项目经历",
  "score": { "gpa": 3.8, "scale": 4.0, "rank_mode": "percent", "percent": 10 },
  "competitions": [
    { "name": "中国大学生计算机设计大赛", "level": "国家级", "award": "二等奖", "year": "2025" }
  ],
  "research": [
    { "type": "project", "title": "医学影像分割项目", "role": "负责人", "venue_or_status": "结题", "year": "2025" }
  ]
}
```

Enums:

- `gender`: `male`, `female`, `other`, `undisclosed`
- `research.type`: `paper`, `project`, `patent`, `other`
- `score.rank_mode`: `none`, `percent`, `ordinal`. Current clients serialize
  structured rank fields only: use `percent` for percentage rank, or
  `rank_position` + `rank_total` for ordinal rank. Do not use the legacy
  display-only `rank` string in API payloads.

### Recommendation

```json
{
  "professor_id": "p_001",
  "name": "张三",
  "university": "上海交通大学",
  "college": "电子信息与电气工程学院",
  "title": "教授",
  "research_fields": ["医学影像", "计算机视觉"],
  "homepage_url": "https://example.edu.cn/zhangsan",
  "match_level": "高",
  "match_score": 0.92,
  "reason": "研究方向与用户需求高度相关。",
  "limitations": ["招生信息以学校官网为准"]
}
```

`match_level` values: `高`, `中`, `低`.

## Endpoints

### POST `/identity/anonymous`

Creates an installation-level anonymous identity. Response data:

```json
{
  "owner_id": "0197...",
  "access_token": "random-secret-token"
}
```

If a valid `scho_anonymous` cookie or bearer token already exists, the endpoint
reuses that owner. Cookie-authenticated Web calls return an empty
`access_token`; Web code must rely on the HttpOnly cookie rather than copying a
bearer into browser storage.

### Conversation sessions

`ConversationSession`:

```json
{
  "id": "0197...",
  "kind": "general",
  "root_session_id": "0197...",
  "source_session_id": null,
  "source_turn_id": null,
  "professor_id": null,
  "revision": 0,
  "title": null,
  "created_at": "2026-06-27T08:00:00Z",
  "updated_at": "2026-06-27T08:00:00Z",
  "deleted_at": null,
  "legacy_context_incomplete": false
}
```

`kind` is `general`, `professor`, or `fork`. Only a `fork` has
`source_session_id` and `source_turn_id`. A professor-anchored conversation
without a valid recommendation source is a `professor` session, never a fake
fork.

- `POST /chat/sessions` creates a `general` or `professor` session. Body:
  `{ "kind": "general", "professor_id": null }`.
- `GET /chat/sessions` returns `{ "items": ConversationSession[] }`, excluding
  fork sessions and deleted sessions.
- `GET /chat/sessions/{id}` returns `{ session, turns, messages }`.
- `GET /chat/sessions/{id}/turns` returns `{ turns, messages }`.
- For a `fork`, both read endpoints return only turns and messages created in
  that fork. The inherited source prefix remains server-side model context and
  is never projected into the fork's visible history.
- `GET /chat/sessions/{rootId}/forks` returns
  `{ "items": ConversationSession[] }`.
  Current Flutter clients also tolerate a legacy raw array for session and fork
  list responses, but new backend responses should use the `{ "items": [...] }`
  shape above.
- `DELETE /chat/sessions/{id}` transactionally deletes the session. Deleting a
  root also deletes its forks, turns, attempts, messages, summaries, and cache;
  deleting a fork does not affect the root.

### POST `/chat/sessions/{sourceId}/forks`

Request:

```json
{ "source_turn_id": "0197...", "professor_id": "p_001" }
```

The source turn must be a completed recommendation turn containing the named
professor. `(sourceId, source_turn_id, professor_id)` is unique per owner, so
concurrent duplicates return the same fork. The fork context is permanently
bounded to the source conversation prefix ending at `source_turn_id`.

### POST `/chat/sessions/{id}/turns`

Headers: `Authorization` and `Idempotency-Key` are required. Request:

```json
{
  "text": "为什么推荐这位导师？",
  "request_id": "0197...",
  "expected_revision": 3
}
```

Response is `text/event-stream`. Every event carries `session_id`, `turn_id`,
`attempt_id`, and `revision`; clients must discard events that do not match the
current operation.

```text
event: ack
data: {"session_id":"...","turn_id":"...","attempt_id":"...","revision":3}

event: route
data: {"session_id":"...","turn_id":"...","attempt_id":"...","revision":3,"route":"conversation"}

event: delta
data: {"session_id":"...","turn_id":"...","attempt_id":"...","revision":3,"text":"主要依据是"}

event: completed
data: {"session_id":"...","turn_id":"...","attempt_id":"...","revision":4,"session":{},"message":{},"quick_actions":[]}

event: error
data: {"session_id":"...","turn_id":"...","attempt_id":"...","revision":3,"code":"revision_conflict","message":"会话已更新"}
```

`route` is `recommendation`, `conversation`, or `forkReroute`.
`completed.message.related_recommendations` and the final session descriptor
are authoritative and must not be discarded by the client.

### Attempts, cancellation, and feedback

- `POST /chat/turns/{turnId}/attempts` regenerates an existing turn without
  adding another user message. Body:
  `{ "session_id": "...", "request_id": "...", "expected_revision": 4 }`.
  It returns the same SSE event grammar as turn submission.
- `POST /chat/attempts/{attemptId}/cancel` persists the active attempt as
  `interrupted`; partial text is not represented as completed.
- `PATCH /chat/messages/{messageId}/feedback` persists
  `{ "feedback": "like" }`, `{ "feedback": "dislike" }`, or
  `{ "feedback": "none" }`.

The server is authoritative in HTTP mode and constructs model context from
stored conversation state. Clients may cache completed aggregates for offline
reading, but do not queue offline sends.

### GET `/home/prompts`

Query:

- `mode`: `mentor` or `competition`

Response data:

```json
[
  { "text": "我想找计算机视觉方向的导师，最好在北京。" }
]
```

### GET `/home/config`

Server-managed home screen copy and prompt chips. HTTP production clients use
this endpoint instead of local hardcoded taglines, quick tags, or prompt tiles.

Query:

- `mode`: `mentor` or `competition`

Response data:

```json
{
  "taglines": ["说说你想研究的方向，我帮你找到合适的导师"],
  "quick_tags": ["计算机视觉", "自然语言处理", "北京"],
  "prompts": [
    { "text": "我想找计算机视觉方向的导师，最好在北京。" }
  ]
}
```

### POST `/recommendations/mentors`

Request:

```json
{
  "prompt": "我想找医学影像和计算机视觉方向的导师。",
  "session_id": "s_123",
  "profile": {}
}
```

`session_id` and `profile` are optional.

Response data:

```json
{
  "session_id": "s_123",
  "query_understanding": {
    "research_interests": ["医学影像", "计算机视觉"],
    "preferred_locations": ["上海"],
    "preferred_universities": [],
    "degree_stage": "硕士",
    "uncertainties": ["未明确偏理论还是应用"]
  },
  "recommendations": [],
  "follow_up_questions": ["偏理论", "偏应用", "只看985", "适合硕士"]
}
```

`follow_up_questions` is a legacy field name. Values should be short quick-action
labels for UI chips, not full questions.

### POST `/recommendations/competitions`

Request:

```json
{
  "prompt": "推荐近期可报名的人工智能竞赛。",
  "session_id": "c_123",
  "profile": {}
}
```

Response data:

```json
{
  "session_id": "c_123",
  "understanding": {
    "directions": ["人工智能"],
    "categories": ["计算机类"],
    "timing_preferences": ["近期可报名"],
    "team_preferences": ["团队赛"],
    "uncertainties": ["未明确可投入时间"]
  },
  "recommendations": [
    {
      "id": "comp_ai_creative",
      "name": "人工智能创新应用大赛",
      "category": "计算机类",
      "level": "国家级",
      "tags": ["AI", "应用"],
      "team_size": "1-5人",
      "signup_time": "以官网通知为准",
      "contest_time": "以官网通知为准",
      "format": "作品赛",
      "organizer": "主办方",
      "official_url": "https://example.com",
      "reason": "方向匹配。",
      "preparation_tips": ["先确定应用场景"],
      "limitations": ["以官网最新通知为准"],
      "match_score": 0.86
    }
  ],
  "follow_up_questions": ["算法赛", "作品赛", "团队赛", "近期可报"]
}
```

`follow_up_questions` is a legacy field name. Values should be short quick-action
labels for UI chips, not full questions.

### GET `/competitions`

Returns the authoritative competition catalog snapshot for HTTP production
clients. Local catalog data is only an offline/LLM-mode fallback.

Response data:

```json
[
  {
    "id": "comp_lanqiao",
    "name": "蓝桥杯全国软件和信息技术专业人才大赛",
    "category": "计算机类",
    "level": "国家级",
    "tags": ["算法", "个人赛"],
    "team_size": "个人",
    "signup_time": "以官网通知为准",
    "contest_time": "以官网通知为准",
    "format": "限时编程",
    "organizer": "主办方",
    "official_url": "https://example.com",
    "reason": "",
    "preparation_tips": [],
    "limitations": ["以官网最新通知为准"],
    "match_score": 0
  }
]
```

### GET `/competitions/{competition_id}`

Returns one competition catalog snapshot. HTTP clients show an error/empty state
when it is missing; they must not guess from local catalog data.

Response data is one `RecommendedCompetition` object with the same shape as the
items from `GET /competitions`.

### GET `/professors/{professor_id}`

Response data:

```json
{
  "professor_id": "p_001",
  "name": "张三",
  "university": "上海交通大学",
  "college": "电子信息与电气工程学院",
  "title": "教授",
  "research_fields": ["医学影像", "计算机视觉"],
  "bio": "主要研究医学影像分析。",
  "homepage_url": "https://example.edu.cn/zhangsan",
  "source_url": "https://example.edu.cn/zhangsan/source",
  "updated_at": "2026-06-01",
  "data_quality_score": 0.87
}
```

### POST `/chat/messages`

> Legacy compatibility endpoint. New clients use the session/turn endpoints
> above.

Request:

```json
{
  "session_id": "s_123",
  "message": "为什么推荐这位导师？",
  "professor_id": "p_001"
}
```

Response data:

```json
{
  "session_id": "s_123",
  "answer": "主要依据是研究方向匹配。",
  "related_recommendations": []
}
```

### GET `/chat/stream`

Query:

- `session_id`: required
- `message`: required
- `professor_id`: optional

Response content type: `text/event-stream`

Events:

```text
event: delta
data: {"text":"主要依据是"}

event: related_recommendations
data: {"items":[]}

event: done
data: {"session_id":"s_123"}

event: error
data: {"code":50001,"message":"服务异常，请稍后重试"}
```

### POST `/chat/route`

Decide whether a follow-up message needs a new round of recommendations (produce cards) or is a plain question about already-recommended mentors.

Request:

```json
{
  "follow_up": "只看上海的导师",
  "session_id": "s_123",
  "last_recommendations": [
    {
      "professor_id": "p_001",
      "name": "张三",
      "university": "上海交通大学",
      "research_fields": ["医学影像", "计算机视觉"]
    }
  ]
}
```

`follow_up` is required. `session_id` and `last_recommendations` are optional;
omit `last_recommendations` on the first turn (no prior recommendation). The
recap carries only routing-relevant fields — see `RecommendationRecap`.

Response data:

```json
{ "need": true }
```

`need` is a JSON boolean. The client degrades to `false` on any failure (non-zero
code, malformed `data`, network/timeout) — "宁可少产卡，不阻断对话".

`RecommendationRecap`:

```json
{
  "professor_id": "p_001",
  "name": "张三",
  "university": "上海交通大学",
  "research_fields": ["医学影像", "计算机视觉"]
}
```

### POST `/chat/quick-actions`

Generate short quick-action chip labels for the input bar above the composer. Called on conversation start and after each conversational turn's stream completes (recommendation turns already carry `follow_up_questions` in their result).

Request:

```json
{
  "follow_up": "只看上海的导师",
  "last_recommendations": [
    {
      "professor_id": "p_001",
      "name": "张三",
      "university": "清华大学",
      "research_fields": ["计算机视觉", "医学影像"]
    }
  ]
}
```

`follow_up` is required (empty string on conversation start). `last_recommendations` is optional; omit on the first turn (no prior recommendation). The recap carries only routing-relevant fields — see `RecommendationRecap` (same shape as `/chat/route`), capped to 5 entries by the client.

Response data:

```json
{
  "quick_actions": ["换一批", "偏应用", "只看985", "适合博士"]
}
```

`quick_actions` should be 1-4 short action labels (≤8 CJK chars each), operation phrases only — no full questions, no question marks, no interrogative prefixes like "你/是否/请问". On empty/missing `quick_actions`, the client hides the chips for that turn. On transport failure, the client falls back to a hardcoded default set.

### POST `/professors/compare`

Request:

```json
{ "professor_ids": ["p_001", "p_002"] }
```

Response data:

```json
{
  "professor_ids": ["p_001", "p_002"],
  "professors": [],
  "rows": [
    { "dimension": "研究方向匹配", "cells": { "p_001": "偏医学影像", "p_002": "偏大模型" } }
  ],
  "summary": "两位导师方向不同。",
  "suggestion": "若更看重医学影像可优先了解 p_001。"
}
```

### POST `/professors/{professor_id}/match-analysis`

Request:

```json
{ "profile": {} }
```

Response data:

```json
{
  "professor_id": "p_001",
  "summary": "方向较契合。",
  "strengths": ["项目经历相关"],
  "gaps": ["需要补充论文阅读"],
  "suggestions": ["阅读导师近三年论文"],
  "dimensions": [
    { "label": "方向契合", "score": 82, "comment": "研究兴趣接近。" }
  ]
}
```

### POST `/professors/{professor_id}/outreach-email`

Request:

```json
{ "profile": {} }
```

Response data:

```json
{ "subject": "关于医学影像方向研究生申请的咨询", "body": "张三教授您好..." }
```

### POST `/profile/achievements/extract`

Request:

```json
{ "raw_text": "我参加过数学建模竞赛，获得省级一等奖。" }
```

Response data:

```json
{
  "competitions": [{ "name": "数学建模竞赛", "level": "省级", "award": "一等奖" }],
  "research": []
}
```

### POST `/preparation-plans/generate`

Generate personalized optional tasks and advice for a competition preparation plan.
The endpoint only returns optional tasks and advice; it does not return the full
plan or perform final date scheduling.

Request:

```json
{
  "competition": {
    "id": "comp_ai_creative",
    "name": "人工智能创新应用大赛",
    "category": "计算机类",
    "rules_summary": {
      "signup_time": "2026-07-01 ~ 2026-09-01",
      "contest_time": "2026-10-01 ~ 2026-11-01",
      "team_size": "1-5人",
      "format": "作品赛",
      "organizer": "主办方",
      "official_url": "https://example.com"
    }
  },
  "calendar_today": "2026-05-01",
  "target_date": "2026-05-30",
  "timeline_type": "submission",
  "event_end_date": null,
  "defense_date": "2026-06-10",
  "weekly_commitment": "hours6to10",
  "experience_level": "intermediate",
  "phase_keys": ["team_formation", "topic_selection", "proposal_writing", "submission_polish", "defense_prep"],
  "user_profile": {}
}
```

`weekly_commitment` values: `hours3to5`, `hours6to10`, `hours11to15`,
`hours16plus`.

`experience_level` values: `beginner`, `intermediate`, `experienced`.

`timeline_type` values: `eventWindow`, `submission`.

- `calendar_today` is `YYYY-MM-DD` (`format: date`) and is the authoritative
  scheduling basis; the server validates format and order against anchors only,
  it does not replace it with a server timezone.
- `target_date`, `event_end_date`, `defense_date` are all `YYYY-MM-DD`
  (`format: date`).
- `event_end_date` is required for `eventWindow` and must be null or omitted for
  `submission`. `defense_date` is only meaningful for `submission` and may be
  null or omitted. Current Flutter clients omit these fields when they are null.
- `phase_keys` is the client-side allowed phase key set. The server must only
  return phases whose `key` is in this set; `defense_prep` appears only when
  `defense_date` is present.
- `user_profile` is optional and follows the `UserProfile` shape.

Response data:

```json
{
  "phases": [
    {
      "key": "proposal_writing",
      "optional_tasks": [
        { "template_key": "ai_benchmark", "title": "补充验证实验", "estimated_hours": 4 }
      ],
      "personalized_advice": "先完成核心链路，再补充对比实验。"
    }
  ],
  "global_advice": "提交前优先保证作品完整可运行。"
}
```

`optional_tasks` contains at most 3 items per phase. `template_key` is optional;
when present it must be unique within the phase. `estimated_hours` is a number.

Common errors: `40001` invalid request, `42201` validation failed, `50001`
server error.

### GET `/preparation/config`

Returns lightweight server-managed preparation strategy config. HTTP production
clients use it for category normalization, default timeline type, and diagnostic
question options.

Response data:

```json
{
  "category_aliases": {
    "电子信息类": "电子与信息类",
    "创新创业类": "综合与创业类"
  },
  "timeline_defaults": {
    "comp_icpc": "eventWindow",
    "comp_lanqiao": "eventWindow"
  },
  "prior_experience_options": ["从没参加", "参加过未获奖", "获得校级以上奖"],
  "domain_familiarity_options": ["不熟", "一般", "熟悉"]
}
```

### GET `/preparation-templates`

Returns the merged preparation template for the selected timeline type, defense
setting, category, and competition. HTTP production clients use this endpoint
instead of asset templates.

Query:

- `timeline_type`: `eventWindow` or `submission`
- `include_defense`: boolean
- `category`: normalized category key
- `competition_id`: competition catalog id

Response data:

```json
{
  "phases": [
    {
      "key": "topic_selection",
      "title": "选题定位",
      "weight": 0.2,
      "required_tasks": [
        { "template_key": "read_rules", "title": "阅读赛事规则", "estimated_hours": 2 }
      ],
      "optional_tasks": []
    }
  ]
}
```

### POST `/preparation-plans/diagnose`

Diagnose the user's experience level for a competition category from a short
two-question Q&A. The returned `level` is an AI suggestion only; the client must
wait for the user to accept or manually override before persisting it.

Request:

```json
{
  "competition": { "id": "comp_icpc", "name": "ICPC", "category": "计算机类", "rules_summary": {} },
  "profile": {},
  "answers": [
    { "question_key": "prior_experience", "answer": "拿过校级以上奖" },
    { "question_key": "domain_familiarity", "answer": "熟悉" }
  ]
}
```

- `competition` is a `CompetitionSnapshot` (same shape as `/generate`).
- `profile` is optional and follows `UserProfile`.
- `answers` is a non-empty list of `{ question_key, answer }` pairs.

Response data:

```json
{
  "level": "intermediate",
  "rationale": "根据你的参赛经历和算法熟悉度，你已具备进阶基础。",
  "suggestion": "建议按进阶档排期；时间充裕时可增加老手档训练。"
}
```

`level` values: `beginner`, `intermediate`, `experienced`. `rationale` is
required; `suggestion` is optional.

### POST `/preparation-plans/{plan_id}/assistant`

Suggest structured plan-change cards for a preparation plan. The path `{plan_id}`
must equal `plan_snapshot.id`; the plan remains local data and the snapshot is
the server's single source of truth for reasoning and validation.

Request:

```json
{
  "calendar_today": "2026-05-01",
  "base_plan_revision": 3,
  "plan_snapshot": {
    "id": "pp_1",
    "revision": 3,
    "competition": {},
    "target_date": "2026-05-30T00:00:00.000",
    "timeline_type": "submission",
    "event_end_date": null,
    "defense_date": "2026-06-10",
    "weekly_commitment": "hours6to10",
    "experience_level": "intermediate",
    "status": "active",
    "phases": [
      {
        "key": "proposal_writing",
        "title": "方案撰写",
        "start_date": "2026-05-10T00:00:00.000",
        "end_date": "2026-05-22T00:00:00.000",
        "tasks": [
          {
            "id": "task_core_algo",
            "template_key": "core_algorithm",
            "title": "核心算法实现",
            "kind": "required",
            "estimated_hours": 16,
            "due_date": "2026-05-15T00:00:00.000",
            "completed_at": null
          }
        ]
      }
    ],
    "created_at": "2026-05-01T08:00:00Z",
    "updated_at": "2026-05-01T08:00:00Z"
  },
  "user_message": "这周期末考没空，往后挪；答辩前留个模拟答辩",
  "request_id": "req_1714521600000",
  "history": [
    {
      "role": "assistant",
      "content": "上一轮回复",
      "card_results": [{ "card_id": "cc_0", "status": "applied" }]
    }
  ]
}
```

- `calendar_today` is `YYYY-MM-DD` (`format: date`).
- `base_plan_revision` is the plan `revision` the suggestion is based on.
- `plan_snapshot` is the full `PreparationPlan.toJson()` shape sent by the
  Flutter client. `target_date`, phase `start_date` / `end_date`, and task
  `due_date` are ISO-8601 strings; current clients send `DateTime.toIso8601String()`
  for these fields. `event_end_date`, `defense_date`, and
  `registration_deadline` are `YYYY-MM-DD` when present. Server validation should
  compare these values by calendar day after parsing.
- `plan_snapshot.status` values: `active`, `archived`.
- `user_message` is the user's free-text adjustment request.
- `history` is optional; each turn carries `role` (`user` or `assistant`),
  `content`, and an optional `card_results` list of `{ card_id, status }`.
- `request_id` 由客户端生成（`req_<ms>`），服务端在响应 `data` 中原样 echo，用于跨抽屉关闭追踪该轮。

Response data:

```json
{
  "reply": "我整理了两项可单独确认的调整。",
  "request_id": "req_1714521600000",
  "change_set": {
    "id": "cs_1",
    "base_plan_revision": 3,
    "cards": [
      {
        "id": "cc_1",
        "type": "move_task",
        "target_task_id": "task_core_algo",
        "new_date": "2026-05-22",
        "summary": "把【核心算法实现】移到 5 月 22 日",
        "rationale": "避开期末考试周，同时仍早于提交 DDL。",
        "status": "pending"
      },
      {
        "id": "cc_2",
        "type": "add_task",
        "target_phase_key": "defense_prep",
        "new_task": {
          "title": "第二次模拟答辩",
          "estimated_hours": 3,
          "due_date": "2026-06-05",
          "note": "记录评委追问"
        },
        "summary": "答辩准备阶段新增一次模拟答辩",
        "rationale": "在正式答辩前预留复盘时间。",
        "status": "pending"
      }
    ]
  }
}
```

- `change_set.cards` is capped at `maxItems: 5`; LLM output beyond 5 is
  truncated (the first 5 are kept, the rest discarded).
- `type` values: `move_task`, `add_task`, `delete_task`, `reschedule_phase`,
  `append_advice`.
- `status` values: `pending`, `rejected`, `applied`, `declined`, `stale`.
- Per-type required fields (validated by the shared `PlanChangeValidator`):
  - `move_task`: `target_task_id`, `new_date`.
  - `add_task`: `target_phase_key`, `new_task` (`new_task.title` non-empty,
    `estimated_hours` an integer in 1–200, `due_date` `YYYY-MM-DD`).
  - `delete_task`: `target_task_id` (only `optional` or `userAdded` tasks).
  - `reschedule_phase`: `phase_schedule` (non-empty list of
    `{ phase_key, start_date, end_date }`, all `YYYY-MM-DD`).
  - `append_advice`: `advice_text` (non-empty); `target_phase_key` optional.
- `summary` and `rationale` are display-only and must not be used to locate
  tasks or execute operations.
- `rejection_code` and `rejection_reason` are present only when
  `status = rejected`. Rejection codes:

| Code | Meaning |
|---|---|
| `missing_required_fields` | Required field for the card type is absent |
| `target_task_not_found` | `target_task_id` not in snapshot |
| `target_phase_not_found` | `target_phase_key` not in snapshot |
| `completed_task_protected` | Task already completed, cannot move/delete |
| `date_out_of_range` | Proposed date outside the legal phase interval |
| `invalid_add_task_fields` | Empty title or `estimated_hours` not 1–200 integer |
| `required_task_delete_forbidden` | `required` tasks cannot be deleted |
| `phase_schedule_invalid` | Merged phase schedule has overlap/order/range error |
| `invalid_advice_fields` | `advice_text` is empty |

JSON parse failure makes the whole call a `Result.failure` and must not write
the plan. The client re-validates each card before applying it (the HTTP backend
is the authoritative validation layer, but the client must not trust it alone).

### Profile

- `GET /profile`: returns `UserProfile`
- `PUT /profile`: request body is `UserProfile`, returns `UserProfile`
- `DELETE /profile`: returns `{ "cleared": true }`

### Favorites

- `GET /favorites`: returns `FavoriteItem[]`
- `PUT /favorites/{professor_id}`: request body is `FavoriteItem`, returns `{ "favorited": true, "item": FavoriteItem }`
- `DELETE /favorites/{professor_id}`: returns `{ "favorited": false }`

`FavoriteItem`:

```json
{
  "professor_id": "p_001",
  "name": "张三",
  "university": "上海交通大学",
  "college": "电子信息与电气工程学院",
  "title": "教授",
  "research_fields": ["医学影像"],
  "homepage_url": "https://example.edu.cn",
  "favorited_at": "2026-06-15T10:00:00Z"
}
```

### History

These history endpoints store generated recommendation searches. Current HTTP
clients write both mentor searches (`type = mentor`) and competition searches
(`type = competition`) to `/history`; conversation transcript history remains
under `/chat/sessions`.

`SearchHistoryItem`:

```json
{
  "type": "mentor",
  "session_id": "s_123",
  "prompt": "医学影像 上海",
  "created_at": "2026-06-15T10:00:00.000Z",
  "summary": "方向：医学影像 / 地区：上海",
  "research_interests": ["医学影像"],
  "preferred_locations": ["上海"],
  "recommendation_count": 3
}
```

Writes may use either `mentor` or `competition`; the backend may normalize or
de-duplicate entries by `session_id`.

#### GET `/history`

Response data is the full search history list, newest first preferred:

```json
[
  {
    "type": "mentor",
    "session_id": "s_123",
    "prompt": "医学影像 上海",
    "created_at": "2026-06-15T10:00:00.000Z",
    "summary": "方向：医学影像 / 地区：上海",
    "research_interests": ["医学影像"],
    "preferred_locations": ["上海"],
    "recommendation_count": 3
  }
]
```

#### POST `/history`

Request body is `SearchHistoryItem`. Response data is the saved
`SearchHistoryItem`; the backend may normalize or de-duplicate by `session_id`.

#### DELETE `/history/{session_id}`

Response data:

```json
{ "removed": true }
```

#### DELETE `/history`

Response data:

```json
{ "cleared": true }
```

### POST `/feedback`

提交用户反馈(bug / 推荐不准 / 导师未收录 / 其他)。请求体:

```json
{
  "id": "uuid",
  "type": "recommendation | missing_professor | bug | other",
  "content": "描述",
  "contact": "可选",
  "context": {
    "route": "/professor/P001",
    "session_id": "...", "message_id": "...",
    "professor_id": "P001", "competition_id": null,
    "prompt": "...", "app_version": "1.2.0", "data_source_mode": "http"
  },
  "created_at": "2026-06-30T12:00:00Z"
}
```

响应遵循统一信封 `{ code, message, data }`。Current Flutter clients only depend on
`code == 0` and ignore `data`; backends may return `null` or this receipt object:

```json
{ "id": "...", "status": "received", "received_at": "2026-06-30T12:00:01Z" }
```

`code != 0` 表示业务失败。客户端不重试、不本地保存。
