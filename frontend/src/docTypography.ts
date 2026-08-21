/* ============================================================
   纪要文档排版引擎 (Doc Typography) — 本地预览用,未提交

   把纪要里的轻量标记渲染成真正的排版:
     ## 标题      → 层级标题(去掉 # 字符)
     **加粗**     → 真实加粗
   只变换展示,数据与 React 状态零接触:
   React 覆写文本时由观察器自动重排,删除本文件即还原。
   ============================================================ */

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function inlineMd(escaped: string): string {
  return escaped.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
}

function typographize(article: HTMLElement): void {
  if (article.dataset.docTyped === "1") return;
  const raw = article.textContent || "";
  if (!raw.trim()) return;
  const html = raw
    .split("\n")
    .map(line => {
      const escaped = escapeHtml(line);
      const heading = escaped.match(/^(#{1,3})\s+(.+)$/);
      if (heading) return `<div class="doc-h doc-h${heading[1].length}">${inlineMd(heading[2])}</div>`;
      if (!escaped.trim()) return `<div class="doc-gap"></div>`;
      return `<div class="doc-line">${inlineMd(escaped)}</div>`;
    })
    .join("");
  article.innerHTML = html;
  article.dataset.docTyped = "1";
}

function sweepTypography(): void {
  // 注意::not(.transcript-document) 是安全边界 —— 转写视图的子元素由
  // React 管理(map 渲染的行),innerHTML 替换会让 React 卸载时 removeChild
  // 崩溃;概览/纪要视图的 children 是纯字符串(React 走 textContent 快路径),
  // 覆写只会整体重置文本,观察器随后重排,两相安全。
  document
    .querySelectorAll<HTMLElement>(".result-body .document-view:not(.transcript-document)")
    .forEach(article => {
      // React 覆写文本后结构化 div 消失、或复用节点换了新原始文本,均需重排
      if (
        article.dataset.docTyped === "1" &&
        (!article.querySelector(".doc-line,.doc-h,.doc-gap") || hasRawMarkup(article))
      ) {
        delete article.dataset.docTyped;
      }
      typographize(article);
    });
}

/* 行内排版:问筑听回答的 **加粗**、历史卡片预览的 ## 前缀。
   只处理显示,消息与卡片文本不变。 */
function inlineTypography(el: HTMLElement): void {
  if (el.dataset.docTyped === "1") return;
  const raw = el.textContent || "";
  if (!raw.trim()) return;
  if (!raw.includes("**") && !/^#{1,3}\s/m.test(raw)) return;
  el.innerHTML = inlineMd(escapeHtml(raw)).replace(/^#{1,3}\s+/gm, "");
  el.dataset.docTyped = "1";
}

/** 排版后文本里不该再有原始标记;还检测得到,说明 React 已覆写内容 */
function hasRawMarkup(el: HTMLElement): boolean {
  const text = el.textContent || "";
  return text.includes("**") || /^#{1,3}\s/m.test(text);
}

function sweepInline(): void {
  document
    .querySelectorAll<HTMLElement>(".assistant-drawer .chat-message.assistant, .history-grid > button p")
    .forEach(el => {
      // React 复用节点换内容(如搜索过滤历史列表)后标记残留;
      // 只查 ** 会漏掉纯 ## 开头的新文本,前缀就会一直挂在卡片上
      if (el.dataset.docTyped === "1" && hasRawMarkup(el)) {
        delete el.dataset.docTyped;
      }
      inlineTypography(el);
    });
}

const SWEEP_SCOPES = ".result-body, .chat-body, .history-grid";
const SWEEP_TARGETS = ".document-view:not(.transcript-document), .chat-message.assistant, .history-grid > button p";

let typographyScheduled = false;
new MutationObserver(records => {
  // childList 记录的 target 是父节点:整段挂载(如历史页)时目标在 addedNodes 的子树里
  const needsSweep =
    records.some(r =>
      [...r.addedNodes].some(
        n => n instanceof HTMLElement && (n.matches(SWEEP_TARGETS) || !!n.querySelector(SWEEP_TARGETS))
      )
    ) ||
    records.some(r => {
      const target = r.target as HTMLElement;
      return !!(target.closest && target.closest(SWEEP_SCOPES));
    });
  if (!needsSweep) return;
  if (typographyScheduled) return;
  typographyScheduled = true;
  requestAnimationFrame(() => {
    typographyScheduled = false;
    sweepTypography();
    sweepInline();
  });
}).observe(document.body, { childList: true, subtree: true });

sweepTypography();
sweepInline();

export {};
