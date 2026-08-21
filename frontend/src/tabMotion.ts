/* ============================================================
   选项卡滑动指示器引擎 (Tab Motion) — 本地预览用,未提交

   为四处选项卡注入一枚共享指示器,以 FLIP 方式在选中项之间滑动:
     .scene-tabs   场景选项卡(下划线)
     .input-tabs   输入方式(下划线)
     .result-tabs  结果选项卡(下划线)
     .auth-tabs    登录/注册(iOS 分段控件滑块)

   只做交互动效,不含任何产品逻辑;随样式层一并移除即还原。
   ============================================================ */

type GlideKind = "underline" | "thumb";

type GlideConfig = {
  selector: string;
  kind: GlideKind;
  /** 下划线相对按钮宽度的单边内缩比例,呼应原设计 24% 的居中短条 */
  inset: number;
};

const GLIDE_GROUPS: GlideConfig[] = [
  { selector: ".scene-tabs", kind: "underline", inset: 0.24 },
  { selector: ".input-tabs", kind: "underline", inset: 0.24 },
  { selector: ".result-tabs", kind: "underline", inset: 0 },
  { selector: ".auth-tabs", kind: "thumb", inset: 0 },
];

const live = new Map<HTMLElement, () => void>();

function activeButtonOf(container: HTMLElement): HTMLButtonElement | null {
  return (
    container.querySelector<HTMLButtonElement>('button[aria-selected="true"]') ??
    container.querySelector<HTMLButtonElement>("button.active")
  );
}

function setupGlide(container: HTMLElement, config: GlideConfig): () => void {
  const indicator = document.createElement("i");
  indicator.className = `tab-glide tab-glide--${config.kind}`;
  indicator.setAttribute("aria-hidden", "true");
  container.appendChild(indicator);

  const place = (animate: boolean): void => {
    const active = activeButtonOf(container);
    if (!active || container.clientWidth === 0) {
      indicator.style.opacity = "0";
      return;
    }
    if (!animate) indicator.classList.add("tab-glide--still");
    const x = active.offsetLeft + active.offsetWidth * config.inset;
    const w = active.offsetWidth * (1 - config.inset * 2);
    indicator.style.opacity = "1";
    indicator.style.width = `${w}px`;
    indicator.style.transform =
      config.kind === "thumb"
        ? `translate(${x}px, ${active.offsetTop}px)`
        : `translateX(${x}px)`;
    if (config.kind === "thumb") indicator.style.height = `${active.offsetHeight}px`;
    if (!animate) {
      requestAnimationFrame(() => indicator.classList.remove("tab-glide--still"));
    }
  };

  // React 改选中态走属性(aria-selected / class);重挂载走 childList
  const mutations = new MutationObserver(records => {
    for (const record of records) {
      if (record.type === "childList") { place(false); return; }
      if (record.type === "attributes") { place(true); return; }
    }
  });
  mutations.observe(container, {
    attributes: true,
    attributeFilter: ["aria-selected", "class"],
    childList: true,
    subtree: true,
  });

  const resizes = new ResizeObserver(() => place(false));
  resizes.observe(container);

  // 点击后下一帧再量:等 React 把新选中态写进 DOM
  const onClick = (): void => { requestAnimationFrame(() => place(true)); };
  container.addEventListener("click", onClick);
  // 网络字体落地后按钮宽度会变,重量一次
  const onLoad = (): void => place(false);
  window.addEventListener("load", onLoad);

  place(false);

  return () => {
    mutations.disconnect();
    resizes.disconnect();
    container.removeEventListener("click", onClick);
    window.removeEventListener("load", onLoad);
    indicator.remove();
  };
}

function scanGlideGroups(): void {
  for (const config of GLIDE_GROUPS) {
    document.querySelectorAll<HTMLElement>(config.selector).forEach(el => {
      if (!live.has(el)) live.set(el, setupGlide(el, config));
    });
  }
  // React 重挂载后旧节点脱离文档,释放其观察者避免泄漏
  for (const [el, cleanup] of live) {
    if (!el.isConnected) {
      cleanup();
      live.delete(el);
    }
  }
}

/* 场景标题/副标题切换时的交叉淡入:
   React 原地替换文本,不触发任何过渡,这里在文本变化时补一拍。 */
const introWatched = new Map<HTMLElement, MutationObserver>();

function watchSceneIntroText(): void {
  document.querySelectorAll<HTMLElement>(".scene-intro h1, .scene-intro p").forEach(el => {
    if (introWatched.has(el)) return;
    const observer = new MutationObserver(() => {
      el.classList.remove("scene-text-swap");
      void el.offsetWidth; // 重启动画
      el.classList.add("scene-text-swap");
    });
    observer.observe(el, { childList: true, characterData: true, subtree: true });
    introWatched.set(el, observer);
  });
  // 视图切换卸载 scene-intro 后,断开对旧节点的观察(与指示器同一套回收策略)
  for (const [el, observer] of introWatched) {
    if (!el.isConnected) {
      observer.disconnect();
      introWatched.delete(el);
    }
  }
}

let scanScheduled = false;
new MutationObserver(() => {
  if (scanScheduled) return;
  scanScheduled = true;
  requestAnimationFrame(() => {
    scanScheduled = false;
    scanGlideGroups();
    watchSceneIntroText();
  });
}).observe(document.body, { childList: true, subtree: true });

scanGlideGroups();
watchSceneIntroText();

export {};
