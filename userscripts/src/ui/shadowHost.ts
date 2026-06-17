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
