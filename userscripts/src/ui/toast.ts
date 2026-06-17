import { mountShadowHost } from './shadowHost';

let toastEl: HTMLDivElement | null = null;
let hideTimer: ReturnType<typeof setTimeout> | null = null;

export function mountToast(): void {
  const shadow = mountShadowHost();
  toastEl = shadow.querySelector('#ycl-toast') as HTMLDivElement | null;
}

export function showToast(msg: string, duration = 3000): void {
  if (!toastEl) return;
  toastEl.textContent = msg;
  toastEl.style.display = 'block';
  if (hideTimer) clearTimeout(hideTimer);
  hideTimer = setTimeout(() => {
    if (toastEl) toastEl.style.display = 'none';
  }, duration);
}
