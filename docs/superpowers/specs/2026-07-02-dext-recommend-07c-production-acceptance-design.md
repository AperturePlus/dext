# 阶段 7c：dext_recommend production acceptance 与放量门禁

> 状态：设计稿
>
> 前置依赖：[R7b HTTP/application state](2026-07-02-dext-recommend-07b-http-app-state-design.md) 完成
>
> 后续阶段：仅在本稿全部门禁通过后进入受控发布

## 1. 目标

用真实 ACTIVE build、真实 runtime adapters 与 owned OpenAPI 路径完成端到端验证。
fixture/fake ports 绿不能替代本阶段，任何门禁失败都禁止上线或继续放量。

## 2. 产物门禁

- catalog build 状态为 ACTIVE，required fact schema/table/columns 全部存在。
- Neo4j active pointer、Qdrant current alias 与 catalog build ID 一致。
- embedding dimension/fingerprint/tokenizer identity、profile hash、expected counts 对账。
- org-unit/profile/role/eligibility/topic coverage 达到 readiness policy；不达标能力必须关闭而非读取 staging。
- ranking/generation profiles 可加载且版本进入 response/eval manifest。

## 3. E2E 与故障注入

- mentor recommendation、detail、conversation、match/email/compare、profile/favorites/history/account cleanup 全链路。
- alias/pointer 切换、catalog 锁、Qdrant/Neo4j/LLM/embedding/Postgres timeout、stream disconnect、进程 shutdown。
- 权限硬门禁：contacts/review/debug/cross-owner leakage 必须为 0；默认 review leakage 为 0；filter correctness 为 100%。
- 任一错误必须结构化且日志无秘密、联系人、用户原文或 embedding 泄漏。

## 4. 离线质量与性能

评测集绑定 build ID、entity/profile hash、ranking/generation profile、taxonomy/fingerprint 与 commit：

- ranking：nDCG@10、Precision@5、Recall@20、top-10 无相关率、ablation regression。
- explanation/filter：explanation precision、filter correctness、review leakage。
- conversation：explicit contract pass rate、implicit routing accuracy。
- generation：grounded precision、no-admission-probability rate、最终 output 清洗。
- performance：p50/p95/p99、dependency phase latency、timeout/cancellation 与 pool saturation。

排序类数值阈值存放在 checked-in eval policy，不写死在 handler；policy 未经产品批准或 baseline 尚未生成时，
本阶段状态保持 blocked。安全、权限、一致性与 filter correctness 是不可放宽的工程硬门禁。

## 5. 发布与回滚

- 先 shadow/offline，再内部流量，再小比例 canary；每步使用同一 eval manifest。
- rollback 只切回上一组已验证 runtime/profile/build 组合，不混搭 build 与 profile。
- rollout 期间 build pointer、错误率、超时、空结果率、权限拒绝率或质量指标越界立即停止并回滚。
- 形成可复现验收报告后，才可将推荐系统状态标为 production-ready。

