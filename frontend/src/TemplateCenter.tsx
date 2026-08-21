import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  Download,
  FileCheck2,
  FileText,
  LayoutTemplate,
  LoaderCircle,
  MapPin,
  Pencil,
  Plus,
  ShieldCheck,
  Trash2,
  Upload,
} from "lucide-react";

type TemplatePlaceholder = {
  raw: string;
  alias: string;
  field: string;
  path: string;
  part: string;
  kind: string;
};

type TemplateStructureItem = {
  path: string;
  part: string;
  kind: string;
  text: string;
  style: string;
};

type InsertionCandidate = {
  id: string;
  path: string;
  placement: "replace" | "after" | "append";
  confidence: number;
  label: string;
  reason: string;
};

type TemplateAnalysis = {
  version: number;
  parse_status: "ready" | "needs_confirmation" | "failed";
  placeholders: TemplatePlaceholder[];
  structure: TemplateStructureItem[];
  insertion_candidates: InsertionCandidate[];
  recommended_target: string;
  recommended_confidence: number;
  risk_messages: string[];
  stats: {
    paragraphs: number;
    placeholders: number;
    recognized_placeholders: number;
    pages_estimate: number;
  };
};

export type CustomTemplate = {
  id: string;
  name: string;
  filename: string;
  created_at: number;
  updated_at: number;
  scenario: string;
  is_default: boolean;
  parse_status: string;
  insertion_strategy: "auto" | "manual" | "append";
  insertion_target: string;
  sha256: string;
  content_size: number;
};

/**
 * 详情接口才带结构解析结果。
 * 列表接口刻意不返回 analysis —— 它是完整的文档大纲 + 占位符 + 插入候选,
 * 实测每条约 33KB,而列表只显示名称、场景与大小。分成两个类型,
 * 误在列表数据上访问 analysis 时 TypeScript 会直接报错。
 */
export type CustomTemplateDetail = CustomTemplate & { analysis: TemplateAnalysis };

const SCENARIOS = [
  ["auto", "全部场景"],
  ["talk", "工程例会"],
  ["general", "通用会议纪要"],
  ["personal", "个人助手"],
  ["interview", "面试记录"],
  ["customer_visit", "客户拜访"],
] as const;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { credentials: "include", ...init });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (response.status === 401) window.dispatchEvent(new CustomEvent("jkinco:auth-expired"));
    throw new Error(body.detail || `请求失败 (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

function formatSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "未知大小";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function sceneLabel(scene: string): string {
  return SCENARIOS.find(([key]) => key === scene)?.[1] || "通用会议纪要";
}

function parseStatusLabel(template: CustomTemplate): string {
  return template.parse_status === "ready" ? "可直接使用" : "需要确认插入位置";
}

export function TemplateCenter({ onTemplatesChanged }: { onTemplatesChanged?: () => void }) {
  const [templates, setTemplates] = useState<CustomTemplate[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<CustomTemplateDetail | null>(null);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadName, setUploadName] = useState("");
  const [uploadScene, setUploadScene] = useState("general");
  const [editingName, setEditingName] = useState("");
  const [editingScene, setEditingScene] = useState("general");
  const [strategy, setStrategy] = useState<"auto" | "manual" | "append">("auto");
  const [target, setTarget] = useState("");
  const [isDefault, setIsDefault] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement | null>(null);

  const loadTemplates = async (preferredId = "") => {
    const result = await request<{ items: CustomTemplate[] }>("/api/custom-templates");
    setTemplates(result.items);
    setSelectedId(current => {
      const candidate = preferredId || current;
      return result.items.some(item => item.id === candidate) ? candidate : result.items[0]?.id || "";
    });
  };

  useEffect(() => {
    void loadTemplates().catch(reason => setError((reason as Error).message));
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let active = true;
    setError("");
    request<CustomTemplateDetail>(`/api/custom-templates/${encodeURIComponent(selectedId)}`)
      .then(item => {
        if (!active) return;
        setDetail(item);
        setEditingName(item.name);
        setEditingScene(item.scenario);
        setStrategy(item.insertion_strategy || "auto");
        setTarget(item.insertion_target || item.analysis.recommended_target || "");
        setIsDefault(item.is_default);
      })
      .catch(reason => active && setError((reason as Error).message));
    return () => { active = false; };
  }, [selectedId]);

  const selectedCandidate = useMemo(
    () => detail?.analysis.insertion_candidates.find(item => item.id === target),
    [detail, target],
  );

  const upload = async () => {
    if (!uploadFile || busy) return;
    setBusy(true);
    setError("");
    setNotice("正在校验 DOCX 结构并分析插入位置...");
    try {
      const form = new FormData();
      form.append("file", uploadFile);
      form.append("name", uploadName.trim() || uploadFile.name.replace(/\.docx$/i, ""));
      form.append("scenario", uploadScene);
      const created = await request<CustomTemplate>("/api/custom-templates", { method: "POST", body: form });
      await loadTemplates(created.id);
      setUploadFile(null);
      setUploadName("");
      if (fileInput.current) fileInput.current.value = "";
      setNotice(created.parse_status === "ready"
        ? "模板校验通过，已可用于生成纪要"
        : "模板已安全保存，请确认纪要插入位置后再使用");
      onTemplatesChanged?.();
    } catch (reason) {
      setNotice("");
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!detail || busy) return;
    setBusy(true);
    setError("");
    try {
      const resolvedTarget = strategy === "auto"
        ? detail.analysis.recommended_target
        : strategy === "append" ? "append:new-page" : target;
      const updated = await request<CustomTemplateDetail>(`/api/custom-templates/${encodeURIComponent(detail.id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: editingName,
          scenario: editingScene,
          is_default: isDefault,
          insertion_strategy: strategy,
          insertion_target: resolvedTarget,
        }),
      });
      await loadTemplates(updated.id);
      setDetail(updated);
      setNotice("模板设置已保存");
      onTemplatesChanged?.();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!detail || busy || !window.confirm(`删除模板“${detail.name}”？已生成的会议和导出记录不会被删除。`)) return;
    setBusy(true);
    setError("");
    try {
      await request<void>(`/api/custom-templates/${encodeURIComponent(detail.id)}`, { method: "DELETE" });
      setDetail(null);
      await loadTemplates();
      setNotice("模板已删除");
      onTemplatesChanged?.();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return <section className="template-center" aria-labelledby="template-center-title">
    <header className="template-center-hero">
      <div>
        <h1 id="template-center-title">自定义 DOCX 模板</h1>
      </div>
      <label className="primary template-upload-trigger">
        <Plus />
        选择 DOCX
        <input
          ref={fileInput}
          type="file"
          accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          onChange={event => {
            const next = event.target.files?.[0] || null;
            setUploadFile(next);
            setUploadName(next?.name.replace(/\.docx$/i, "") || "");
            setError("");
            setNotice("");
          }}
        />
      </label>
    </header>

    {uploadFile && <section className="template-upload-panel" aria-label="上传模板">
      <div className="template-file-summary"><FileText /><span><b>{uploadFile.name}</b><small>{formatSize(uploadFile.size)}</small></span></div>
      <label><span>模板名称</span><input maxLength={60} value={uploadName} onChange={event => setUploadName(event.target.value)} /></label>
      <label><span>适用场景</span><select value={uploadScene} onChange={event => setUploadScene(event.target.value)}>{SCENARIOS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <div className="template-upload-actions">
        <button className="secondary" onClick={() => { setUploadFile(null); if (fileInput.current) fileInput.current.value = ""; }}>取消</button>
        <button className="primary" disabled={busy || !uploadName.trim()} onClick={() => void upload()}>{busy ? <LoaderCircle className="spin" /> : <Upload />}上传并解析</button>
      </div>
    </section>}

    {error && <div className="template-message error" role="alert"><AlertTriangle />{error}</div>}
    {notice && <div className="template-message success" role="status"><Check />{notice}</div>}

    <div className="template-center-layout">
      <aside className="template-library" aria-label="我的模板">
        <header><div><h2>我的模板</h2><span>{templates.length}</span></div></header>
        <div className="template-library-list">
          {templates.map(item => <button
            key={item.id}
            className={selectedId === item.id ? "active" : ""}
            onClick={() => setSelectedId(item.id)}
          >
            <span className={`template-status-icon ${item.parse_status === "ready" ? "ready" : "attention"}`}>{item.parse_status === "ready" ? <FileCheck2 /> : <AlertTriangle />}</span>
            <span><b>{item.name}</b><small>{sceneLabel(item.scenario)} · {formatSize(item.content_size)}</small></span>
            {item.is_default && <em>默认</em>}
          </button>)}
          {!templates.length && <div className="template-library-empty"><LayoutTemplate /><b>还没有自定义模板</b></div>}
        </div>
      </aside>

      <main className="template-detail">
        {!detail ? <div className="template-detail-empty"><LayoutTemplate /><h2>选择一个模板查看解析结果</h2></div> : <>
          <header className="template-detail-header">
            <div>
              <span className={`template-parse-state ${detail.parse_status === "ready" ? "ready" : "attention"}`}>{detail.parse_status === "ready" ? <ShieldCheck /> : <AlertTriangle />}{parseStatusLabel(detail)}</span>
              <h2>{detail.name}</h2>
              <p>{detail.filename} · 最近更新 {new Date(detail.updated_at * 1000).toLocaleString("zh-CN")}</p>
            </div>
            <div className="template-detail-actions">
              <a className="secondary" href={`/api/custom-templates/${encodeURIComponent(detail.id)}/download`}><Download />下载原模板</a>
              <button className="template-remove" aria-label="删除模板" onClick={() => void remove()}><Trash2 /></button>
            </div>
          </header>

          <section className="template-settings">
            <h3><Pencil />基本设置</h3>
            <div className="template-settings-grid">
              <label><span>模板名称</span><input maxLength={60} value={editingName} onChange={event => setEditingName(event.target.value)} /></label>
              <label><span>适用场景</span><select value={editingScene} onChange={event => setEditingScene(event.target.value)}>{SCENARIOS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
              <label className="template-default-toggle"><input type="checkbox" checked={isDefault} onChange={event => setIsDefault(event.target.checked)} /><span><b>设为该场景默认模板</b></span></label>
            </div>
          </section>

          <section className="template-analysis">
            <div className="template-analysis-heading"><div><h3>文档结构识别</h3></div><div className="template-stats"><span><b>{detail.analysis.stats.pages_estimate}</b>页估算</span><span><b>{detail.analysis.stats.paragraphs}</b>个结构项</span><span><b>{detail.analysis.stats.recognized_placeholders}</b>个字段</span></div></div>
            {detail.analysis.risk_messages.length > 0 && <div className="template-risks"><AlertTriangle /><div><b>需要关注</b>{detail.analysis.risk_messages.map(message => <p key={message}>{message}</p>)}</div></div>}
            <div className="template-analysis-grid">
              <div className="template-placeholder-list">
                <h4>已识别字段</h4>
                {detail.analysis.placeholders.map((placeholder, index) => <div key={`${placeholder.path}-${index}`}><code>{placeholder.raw}</code><span>{placeholder.field ? `映射为 ${placeholder.field}` : "未知字段，将写入待确认"}</span><small>{placeholder.part} · {placeholder.kind}</small></div>)}
                {!detail.analysis.placeholders.length && <p className="template-muted">未发现占位符，将根据标题和文档结构定位。</p>}
              </div>
              <div className="template-structure-preview">
                <h4>原模板结构预览</h4>
                <div>{detail.analysis.structure.slice(0, 30).map(item => {
                  const isRecommended = detail.analysis.recommended_target.endsWith(item.path);
                  const isSelected = strategy === "manual" && target.endsWith(item.path);
                  return <article key={item.path} className={isRecommended || isSelected ? "marked" : ""}><span>{item.kind === "table_cell" ? "表格单元格" : item.part}</span><p>{item.text}</p>{isRecommended && <em>建议插入点</em>}{isSelected && !isRecommended && <em>人工插入点</em>}</article>;
                })}</div>
              </div>
            </div>
          </section>

          <section className="template-insertion">
            <h3><MapPin />纪要插入方式</h3>
            <div className="template-strategy-options">
              <label><input type="radio" checked={strategy === "auto"} onChange={() => { setStrategy("auto"); setTarget(detail.analysis.recommended_target); }} /><span><b>系统自动定位</b><small>{detail.analysis.insertion_candidates.find(item => item.id === detail.analysis.recommended_target)?.label || "使用安全追加位置"}</small></span></label>
              <label><input type="radio" checked={strategy === "manual"} onChange={() => setStrategy("manual")} /><span><b>人工指定位置</b></span></label>
              <label><input type="radio" checked={strategy === "append"} onChange={() => { setStrategy("append"); setTarget("append:new-page"); }} /><span><b>保留原文并另起一页</b></span></label>
            </div>
            {strategy === "manual" && <label className="template-target-select"><span>指定插入位置</span><select value={target} onChange={event => setTarget(event.target.value)}>{detail.analysis.insertion_candidates.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select><small>{selectedCandidate?.reason}</small></label>}
            <div className="template-save-row"><span>{strategy === "auto" ? "自动定位" : strategy === "manual" ? "人工指定" : "新页追加"} · {strategy === "append" ? "在模板末尾新增页面" : (selectedCandidate?.label || detail.analysis.insertion_candidates.find(item => item.id === detail.analysis.recommended_target)?.label)}</span><button className="primary" disabled={busy || !editingName.trim() || (strategy === "manual" && !target)} onClick={() => void save()}>{busy ? <LoaderCircle className="spin" /> : <Check />}保存模板设置</button></div>
          </section>
        </>}
      </main>
    </div>
  </section>;
}
