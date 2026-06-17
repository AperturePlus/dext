# Plan: detail_extract 阶段不等待图片加载

## 背景
`userscripts/src/actions.ts` 的 `captureCurrentHtml` -> `waitForCaptureReady` 在回传 HTML 前会等待页面“加载完成 + 签名稳定”。

当前实现中真正拖慢 detail_extract 的是：
1. `waitForCaptureReady` 的就绪门槛：`document.readyState === 'complete'`。`complete` 状态需要等待所有资源（含图片）的 `load` 事件，老师照片慢会直接拖到 `CAPTURE_MAX_WAIT`（8s）才超时返回。
2. `captureSignature` 包含 `document.images.length`。它本身只数 `<img>` 元素数量（不等 load），但 detail_extract 时如果页面有懒加载图片，滚动后图片元素出现会让签名变化、重置稳定计数，间接拖慢。

用户诉求：detail_extract 阶段不要检查图片是否加载完成，只检查其它内容是否加载完成。

## 改动文件
仅 `userscripts/src/actions.ts`（约 466–505 行）。

## 改动方案
让 `waitForCaptureReady` / `captureSignature` 感知 intent。当 `intent === 'detail_extract'` 时：
- 就绪门槛从 `document.readyState === 'complete'` 改为 `document.readyState !== 'loading'`（即 `interactive` 即可，不再等图片 load）。
- `captureSignature` 不再计入 `imageCount`，避免懒加载图片元素扰动稳定性判定。

其它 intent 保持原行为（仍要求 `complete`，仍纳入 imageCount），避免影响列表页/分页抓取的稳定性判定。

### 具体代码改动
1. `captureCurrentHtml(job)`（actions.ts:466）
   - 计算 `const skipImages = job.context.intent === 'detail_extract';`
   - 调用 `waitForCaptureReady(skipImages)`。

2. `waitForCaptureReady(skipImages: boolean = false)`（actions.ts:471）
   - 把 `if (document.readyState === 'complete')` 改为
     `if (skipImages ? document.readyState !== 'loading' : document.readyState === 'complete')`。
   - `captureSignature()` 调用改为 `captureSignature(skipImages)`。

3. `captureSignature(skipImages: boolean = false)`（actions.ts:500）
   - `const imageCount = skipImages ? 0 : document.images.length;`
   - 仍返回 `${textLength}:${nodeCount}:${imageCount}`（detail_extract 时 imageCount 恒为 0，等于不计入）。

## 不改动 / 保持不变
- `serializePageWithoutOverlay` 不变：仍照常克隆整页 HTML（图片 `<img>` 标签照常保留，只是不等其 load）。
- 非 detail_extract 的 intent 行为完全不变。
- 不新增依赖、不改类型定义。

## 验证
1. 构建：在 `userscripts/` 下 `npm run build`（或现有 build 脚本）确认 TS 通过、产物生成。
2. 类型检查/测试：`npm test`（utils.test.mjs）应仍通过（未涉及 captureSignature 测试）。
3. 手动回归：detail_extract 任务提交应在文本/节点稳定后即返回，不再被慢照片拖到 8s 超时；列表页/分页任务行为不变。

## 风险
- detail_extract 时部分图片可能仍处于加载中即被采集。但 HTML 中 `<img>` 标签与 `src`/`data-src` 已存在，后端/LLM 仍可解析图片 URL，符合“不阻塞在图片 load”的预期。
