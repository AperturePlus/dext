# 将 Assistant 面板挂入 Shadow DOM

## 目标
把 `#ycl-panel` 面板与 `#ycl-toast` 提示整体迁移进一个 **closed** Shadow DOM，样式注入 shadow root，事件改为在 shadow root 内查找元素，避免页面全局 CSS 污染。

## 现状摘要
- `src/main.ts:1` `import './style.css'` → vite-plugin-monkey 经 `GM_addStyle` 全局注入。
- `src/ui/panel.ts`：`mountPanel()` 建 `<div id="ycl-panel">` 挂到 `document.body`；`renderPanel()` 重写 `innerHTML` 后 `bindEvents()` 用 `document.getElementById` 绑事件。
- `src/ui/toast.ts`：`<div id="ycl-toast">` 挂到 `document.body`。
- `src/style.css`：含 `#ycl-panel` 与 `#ycl-toast` 全部样式（全局选择器）。
- `src/htmlCleanup.ts:23`：捕获清理用 `querySelectorAll('#ycl-panel,#ycl-toast,[data-yanclaw-overlay]')`。
- 面板内部 ID：`ycl-min` `ycl-copy` `ycl-open` `ycl-submit` `ycl-skip` `ycl-fail` `ycl-override` `ycl-decision-switch` `ycl-auto` `ycl-pause`，均由 `panel.ts:bindEvents()` 绑定。

## 方案
单一 host 元素承载 closed shadow root，`<style>` + `#ycl-panel` + `#ycl-toast` 全部置于其中。host 仅作容器（不 styling），面板/提示靠内部 `position:fixed` 脱离 host 盒模型定位。host 标记 `data-yanclaw-overlay` 供捕获清理移除。

### 文件改动

#### 1. 新增 `src/ui/shadowHost.ts`
集中管理 shadow 宿主，供 panel/toast 共用。
```ts
import styleCss from '../style.css?inline';

let shadow: ShadowRoot | null = null;

export function mountShadowHost(): ShadowRoot {
  if (shadow) return shadow;
  const host = document.createElement('div');
  host.dataset.yanclawOverlay = '';
  shadow = host.attachShadow({ mode: 'closed' });
  shadow.innerHTML = `<style>${styleCss}</style><div id="ycl-panel"></div><div id="ycl-toast"></div>`;
  document.body.appendChild(host);
  return shadow;
}

export function getShadowRoot(): ShadowRoot | null {
  return shadow;
}
```
- `?inline` 让 Vite 把 CSS 作为字符串内联（经压缩，与现状一致）；若 monkey 插件异常则回退 `?raw`。
- closed 模式：外部 `host.shadowRoot` 为 null，但我们保留自身 `shadow` 引用。

#### 2. 改 `src/ui/panel.ts`
- `mountPanel()`：调用 `mountShadowHost()`，`panelEl = shadow.querySelector('#ycl-panel')`（改为 inner `#ycl-panel`）。保留 panelEl 上的 click→restore 监听（逻辑不变：minimized 时 innerHTML 为空，target===currentTarget 触发还原）。
- `renderPanel()`：不变，仍操作 `panelEl.className` / `panelEl.innerHTML`。
- `bindEvents()`：`bind` 与 `ycl-min` 的 `document.getElementById` 改为 `shadow.getElementById`（用 `getShadowRoot()`）。

#### 3. 改 `src/ui/toast.ts`
- `mountToast()`：调用 `mountShadowHost()`，`toastEl = shadow.querySelector('#ycl-toast')`。其余 `showToast` 逻辑不变。

#### 4. 改 `src/main.ts`
- 删除 `import './style.css'`（改为由 shadowHost 内联注入）。其余不变。

#### 5. `src/style.css`
- 无需改动。`#ycl-panel`/`#ycl-toast` 选择器在 shadow 内生效；`:host` 不需要，因为 host 不被样式化，固定定位由内部 `#ycl-panel`/`#ycl-toast` 承担（与现状一致）。

#### 6. `src/htmlCleanup.ts`
- 无需改动。host 带 `data-yanclaw-overlay`，现有选择器中的 `[data-yanclaw-overlay]` 仍命中并移除整个 host（含 shadow）。closed shadow 内容本就不会被 `cloneNode/outerHTML` 序列化，捕获更干净。

## 关键点 / 风险
- **closed shadow 引用**：必须保存 `attachShadow` 返回值；不能用 `host.shadowRoot`。
- **`?inline` 兼容**：vite-plugin-monkey ~7.1 基于 Vite 6，`?inline` 为核心特性，预期可用。构建验证。
- **事件重绑**：仍每次 `renderPanel` 后重绑（innerHTML 重建），仅查找入口由 `document` 换 `shadow`。
- **restore 点击**：handler 绑在 inner `#ycl-panel`，shadow 内 event 不被 retarget，`event.target===event.currentTarget` 在 minimized（空内容）时成立。
- **z-index**：`#ycl-panel`/`#ycl-toast` 的 `z-index:2147483647` 在 shadow 内依旧生效（参与宿主页 stacking context）。
- **toast 时序**：`main.ts` 先 `mountToast()` 后 `mountPanel()`，两者都调用幂等 `mountShadowHost()`，首次调用建 host+shadow+两个空容器，第二次直接复用。

## 验证
1. `cd userscripts && npm run build`（`tsc && vite build`）通过。
2. 装载 `dist/yanclaw-assistant.user.js`，在匹配页面确认：
   - 面板/提示正常显示与原样式一致；
   - 最小化/还原、各按钮、自动/暂停 toggle 正常；
   - DevTools 中 `document.getElementById('ycl-panel')` 为 null（已入 shadow），host 元素 `shadowRoot` 为 null（closed）；
   - 页面全局样式（如重置 CSS）不影响面板外观。
3. `npm test` 通过（utils 测试不涉及 UI）。

## 影响文件清单
- 新增：`userscripts/src/ui/shadowHost.ts`
- 修改：`userscripts/src/ui/panel.ts`、`userscripts/src/ui/toast.ts`、`userscripts/src/main.ts`
- 不变：`userscripts/src/style.css`、`userscripts/src/htmlCleanup.ts`、`userscripts/src/ui/panelSections.ts`
