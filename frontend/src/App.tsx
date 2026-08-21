import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import {
  Archive, AudioLines, Bot, Camera, Check, ChevronDown, CircleUserRound, Compass, Download, FileAudio,
  FileText, History, LayoutTemplate, LogOut, Menu, Mic, Moon, Play,
  Search, Send, Sparkles, Square, Upload, Video, X, RefreshCw,
} from "lucide-react";
import type { JoinResponse } from "./MeetingModule";
import { TemplateCenter, type CustomTemplate } from "./TemplateCenter";
import { startRealtimeAsr, type RealtimeAsrHandle } from "./realtimeAsr";
import {
  appendRecordingChunk,
  createRecordingDraft,
  pruneRecordingDrafts,
  deleteRecordingDraft,
  getLatestRecordingDraft,
  readRecordingDraft,
  updateRecordingDraft,
  type RecordingDraftMeta,
} from "./recordingDraftStore";

const SoundSpecimen = lazy(() => import("./SoundSpecimen").then(module => ({ default: module.SoundSpecimen })));
const MeetingLobby = lazy(() => import("./MeetingModule").then(module => ({ default: module.MeetingLobby })));
const MeetingRoom = lazy(() => import("./MeetingModule").then(module => ({ default: module.MeetingRoom })));

type Skin = "classic" | "atlas";

/**
 * 两套外观,名字都取自制图学 —— 呼应「把易逝之物制成可归档之物」这条主线。
 *   经纬 Meridian：清晰、精确、克制的工作界面,现行默认。
 *   图志 Atlas：纸本、宋体、声音标本,取自古图版的知识累积感。
 */
const SKIN_META: Record<Skin, { zh: string; en: string; hint: string }> = {
  classic: { zh: "经纬", en: "Meridian", hint: "清晰精确的工作界面" },
  atlas: { zh: "暗夜", en: "Nocturne", hint: "深色专业工作界面" },
};

// 访客会话时长(小时),仅用于登录页文案;真实有效期由后端 JKINCO_GUEST_SESSION_TTL 决定。
const GUEST_HOURS = 4;
const SKIN_KEY = "jkinco:skin";
const VIEW_KEY = "jkinco:view";
const ACTIVE_MEETING_KEY = "jkinco:active-meeting";
const WORKSPACE_KEY = "jkinco:workspace";
const readSkin = (): Skin => (localStorage.getItem(SKIN_KEY) === "atlas" ? "atlas" : "classic");
const applySkin = (skin: Skin) => { document.documentElement.setAttribute("data-skin", skin); };

function AppFooter() {
  return <footer className="app-footer">
    <span>筑听 · 开源本地版（Open Edition）— 100% 本地运行，数据不出本机</span>
  </footer>;
}

type User = { username: string; display_name: string; role: string; avatar_data?: string };
type Scene = "auto" | "talk" | "general" | "personal" | "interview" | "customer_visit";
type Stage = "input" | "asr" | "model" | "review" | "push";
type View = "workspace" | "history" | "meetings" | "templates";
const readView = (): View => {
  const saved = sessionStorage.getItem(VIEW_KEY);
  return saved === "history" || saved === "meetings" || saved === "templates" ? saved : "workspace";
};
type Meeting = {
  id: string; title: string; created_at: number; mode: Scene; mode_label: string; source: string;
  transcript: string; summary: string; overview: string; status: string; draft?: boolean;
  realtime_meeting_id?: string; read_only?: boolean; custom_template_id?: string; custom_template_name?: string; minutes_status?: string;
};
type Job = { status: string; stage: Stage; progress: number; message: string; error?: string; result?: Partial<Meeting> & { record_id: string; reason: string } };
type ChatMessage = { role: "assistant" | "user"; content: string };
type LiveTranscriptRow = {
  id: string;
  text: string;
  elapsedSeconds?: number;
  interim?: boolean;
};
type ActiveRecording = {
  // 是否由用户主动点「停止录音」结束。只有这种情况才自动进入处理 ——
  // 切场景、开新录音、组件卸载同样会走收尾流程,那些时候用户并没有要出纪要,
  // 自动跑起来会白占任务配额、白花模型调用,而面板已经被重置、他还看不见。
  stoppedByUser?: boolean;
  id: string;
  filename: string;
  mimeType: string;
  stream: MediaStream;
  recorder: MediaRecorder;
  generation: number;
  startedAtEpoch: number;
  elapsedSeconds: number;
  chunkIndex: number;
  persistedChunkCount: number;
  fallbackChunks: Blob[];
  writeChain: Promise<void>;
  persistenceEnabled: boolean;
  transcript: string;
  transcriptRows: LiveTranscriptRow[];
  transcriptSequence: number;
  metaTimer?: number;
  stopping?: boolean;
  finalizePromise?: Promise<File | null>;
  finalized?: boolean;
};

type RecorderPanelProps = {
  scene: Scene;
  ownerUsername: string;
  onCompleted: (m: Meeting) => void;
  onLiveTranscript: (text: string, rows?: LiveTranscriptRow[]) => void;
  onRecordingStateChange: (recording: boolean) => void;
  setStage: (s: Stage) => void;
  setProgress: (n: number) => void;
  // 「是否正在处理」必须由处理方自己上报,不能在外面拿进度反推:任务失败时进度
  // 停在中途(既不到 100 也不归零),反推出来的「生成中」会永远挂着不消失。
  onProcessingChange: (processing: boolean) => void;
  /** 正在查看一场已完成的会议(而不是准备录新的)。此时整套录音输入都不该出现 */
  viewingArchived: boolean;
  onStartNew: () => void;
  initialMode: "device" | "upload" | "live";
  sessionKey: number;
  onManageTemplates: () => void;
};

const MAX_LIVE_PREVIEW_CHARS = 12_000;
const MAX_LIVE_TRANSCRIPT_ROWS = 500;
const RECORDING_DRAFT_META_DELAY = 2_500;

const scenes: { key: Scene; label: string; short: string }[] = [
  { key: "auto", label: "智能识别", short: "自动判断会议场景" },
  { key: "talk", label: "工程例会", short: "施工、监理、建设与现场工程管理" },
  { key: "general", label: "通用会议纪要", short: "管理汇报、项目协同与其他普通会议" },
  { key: "personal", label: "个人助手", short: "个人备忘录与工作复盘" },
  { key: "interview", label: "面试记录", short: "候选人面试与评价" },
  { key: "customer_visit", label: "客户拜访", short: "客户沟通与商机跟进" },
];
const normalizeScene = (mode?: string): Scene => mode === "lingxi" ? "general" : scenes.some(item => item.key === mode) ? mode as Scene : "auto";
/**
 * 统一处理旧版数据兼容:早期的 lingxi(灵犀/管理简报)已并入通用会议纪要。
 * 历史记录、会议详情、实时会议记录三个入口都要走这里,兼容规则只维护一处。
 */
const normalizeMeeting = <T extends { mode: Scene; mode_label: string }>(item: T): T => ({
  ...item,
  mode: normalizeScene(item.mode),
  mode_label: String(item.mode) === "lingxi" ? "通用会议纪要" : item.mode_label,
});
const workflow: { key: Stage; label: string }[] = [
  { key: "input", label: "录音输入" }, { key: "asr", label: "语音转写" },
  { key: "model", label: "模型润色" }, { key: "review", label: "人工校核" },
  { key: "push", label: "推送导出" },
];

/** 未提交录音的时长文案。不足一分钟要按秒说 —— 「0 分钟」会让人以为没录上。 */
function formatDraftLength(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `已录 ${total} 秒`;
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return rest ? `已录 ${minutes} 分 ${rest} 秒` : `已录 ${minutes} 分钟`;
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { credentials: "include", ...init });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (response.status === 401 && path !== "/api/auth/login") {
      window.dispatchEvent(new CustomEvent("jkinco:auth-expired"));
    }
    throw new Error(body.detail || `请求失败 (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [captcha, setCaptcha] = useState({ token: "", image: "" });
  const [captchaAnswer, setCaptchaAnswer] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const refreshCaptcha = () => api<{ token: string; image: string }>("/api/auth/captcha").then(setCaptcha).catch(() => setError("验证码加载失败"));
  useEffect(() => { if (mode === "register") void refreshCaptcha(); }, [mode]);
  const enterAsGuest = async () => {
    setLoading(true); setError("");
    try {
      const data = await api<{ user: User }>("/api/auth/guest", { method: "POST" });
      onLogin(data.user);
    } catch (e) { setError((e as Error).message); } finally { setLoading(false); }
  };
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setLoading(true); setError("");
    try {
      if (mode === "register" && password !== confirmPassword) throw new Error("两次输入的密码不一致");
      const data = await api<{ user: User }>(mode === "login" ? "/api/auth/login" : "/api/auth/register", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(mode === "login" ? { username, password } : {
          username, display_name: displayName, password, captcha_token: captcha.token, captcha_answer: captchaAnswer,
        }),
      });
      onLogin(data.user);
    } catch (e) { setError((e as Error).message); if (mode === "register") void refreshCaptcha(); } finally { setLoading(false); }
  };
  return <main className="login-page">
    <form className="login-card" onSubmit={submit}>
      <img className="login-logo" src="/jkinco-listen-logo.png" alt="JKinco Listen 筑听" />
      <h1>JKinco Listen 筑听 · 开源版</h1>
      <p className="login-sub">本地优先的智能会议纪要与场景化语音工作台</p>
      <div className="auth-tabs"><button type="button" className={mode === "login" ? "active" : ""} onClick={() => { setMode("login"); setError(""); }}>登录</button><button type="button" className={mode === "register" ? "active" : ""} onClick={() => { setMode("register"); setError(""); }}>注册</button></div>
      <label>用户名<input required minLength={3} value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" placeholder="3-32位字母、数字或下划线" /></label>
      {mode === "register" && <label>姓名<input required value={displayName} onChange={e => setDisplayName(e.target.value)} autoComplete="name" placeholder="会议中显示的名称" /></label>}
      <label>密码<input required minLength={mode === "register" ? 8 : undefined} type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete={mode === "login" ? "current-password" : "new-password"} placeholder={mode === "register" ? "至少8位" : ""} /></label>
      {mode === "register" && <><label>确认密码<input required minLength={8} type="password" value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)} autoComplete="new-password" /></label><label>验证码<div className="captcha-row"><input required inputMode="numeric" value={captchaAnswer} onChange={e => setCaptchaAnswer(e.target.value)} placeholder="输入计算结果" /><button type="button" onClick={refreshCaptcha} title="换一张"><img src={captcha.image} alt="图形验证码" /></button></div></label></>}
      {error && <div className="form-error">{error}</div>}
      <button className="primary login-submit" disabled={loading}>{loading ? "正在处理..." : mode === "login" ? "登录" : "注册并登录"}</button>
      <div className="guest-entry">
        <span>先体验一下？</span>
        <button type="button" onClick={enterAsGuest} disabled={loading}>以访客身份进入</button>
        <small>访客数据仅本人可见，{GUEST_HOURS} 小时后自动清除</small>
      </div>
    </form>
    <AppFooter />
  </main>;
}

function Sidebar({ meetings, selected, onSelect, collapsed, onClose, view, composing, onMinutes, onHistory, onMeetings }: {
  meetings: Meeting[]; selected?: string;
  onSelect: (id: string) => void; collapsed: boolean; onClose: () => void; view: View;
  /** 正在新建一场录音纪要。看已完成的会议同样停在 workspace,但那不是「录音纪要」 */
  composing: boolean;
  onMinutes: () => void; onHistory: () => void; onMeetings?: () => void;
}) {
  return <aside className={`sidebar ${collapsed ? "collapsed" : ""}`} aria-label="主导航">
    <div className="sidebar-brand"><img src="/jkinco-listen-logo.png" alt="JKinco Listen 筑听" /><button className="icon mobile-only" aria-label="关闭导航" onClick={onClose}><X /></button></div>
    <button className="start-meeting-button" onClick={onMeetings}><Video />实时会议</button>
    <nav className="main-nav">
      {/* 只在真正新建录音时点亮:打开一场历史会议同样停在 workspace,若照旧点亮,
          「录音纪要」和下面选中的那条最近会议会同时高亮,指向两件事,反而看不出
          自己在哪儿 —— 这个入口只代表「新建一场录音纪要」。 */}
      <button className={composing ? "active" : ""} aria-current={composing ? "page" : undefined} onClick={onMinutes}><FileText />录音纪要</button>
      <button className={view === "history" ? "active" : ""} aria-current={view === "history" ? "page" : undefined} onClick={onHistory}><Archive />历史会议</button>
    </nav>
    <div className="nav-heading recent-heading"><span>最近会议</span><History size={14} /></div>
    <div className="recent-list">{meetings.slice(0, 6).map(item => <button key={item.id} className={selected === item.id ? "active" : ""} onClick={() => onSelect(item.id)}><span className="recent-dot" /><span><b>{item.title}</b><small>{item.mode_label} · {new Date(item.created_at * 1000).toLocaleDateString("zh-CN")}</small></span></button>)}</div>
    <button className={`recent-view-all ${view === "history" ? "active" : ""}`} aria-current={view === "history" ? "page" : undefined} onClick={onHistory}><span>查看全部</span><small>{meetings.length} 条</small></button>
  </aside>;
}

function SkinToggle({ skin, onToggle }: { skin: Skin; onToggle: () => void }) {
  const current = SKIN_META[skin];
  const next = SKIN_META[skin === "classic" ? "atlas" : "classic"];
  return <button
    className="skin-toggle"
    onClick={onToggle}
    title={`当前 ${current.zh} ${current.en} · 切换到 ${next.zh} ${next.en}（${next.hint}）`}
    aria-label={`当前外观 ${current.zh} ${current.en},点击切换到 ${next.zh} ${next.en}`}
  >
    {skin === "classic" ? <Compass /> : <Moon />}
    <span className="skin-name">{current.en}</span>
  </button>;
}

function Header({ user, query, setQuery, onLogout, openSidebar, onProfile, skin, onToggleSkin }: { user: User; query: string; setQuery: (s: string) => void; onLogout: () => void; openSidebar: () => void; onProfile: () => void; skin: Skin; onToggleSkin: () => void }) {
  const [menu, setMenu] = useState(false);
  return <header className="topbar">
    <div className="topbar-title"><button className="icon mobile-menu" aria-label="打开导航" onClick={openSidebar}><Menu /></button><div><h2>筑听工作台</h2></div></div>
    <div className="topbar-actions"><div className="search-box"><Search /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索会议、摘要、待办..." /></div>
      <SkinToggle skin={skin} onToggle={onToggleSkin} />
      <div className="user-menu"><button aria-label="账户菜单" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu(v => !v)}>{user.avatar_data ? <img className="avatar avatar-image" src={user.avatar_data} alt="个人头像" /> : <span className="avatar">{user.display_name.slice(0, 1)}</span>}<span>{user.display_name}</span><ChevronDown /></button>{menu && <div className="user-popover" role="menu"><button role="menuitem" onClick={() => { setMenu(false); onProfile(); }}><CircleUserRound />个人信息</button><button role="menuitem" onClick={onLogout}><LogOut />退出登录</button></div>}</div>
    </div>
  </header>;
}

function ProfileModal({ user, open, onClose, onSaved }: { user: User; open: boolean; onClose: () => void; onSaved: (user: User) => void }) {
  const [name, setName] = useState(user.display_name); const [avatar, setAvatar] = useState<File | null>(null); const [preview, setPreview] = useState(user.avatar_data || ""); const [error, setError] = useState(""); const [saving, setSaving] = useState(false);
  // 修改密码与资料保存互不相干:各自独立提交、独立报错,避免改名失败连带把密码表单清空
  const [currentPassword, setCurrentPassword] = useState(""); const [newPassword, setNewPassword] = useState(""); const [repeatPassword, setRepeatPassword] = useState("");
  const [passwordError, setPasswordError] = useState(""); const [passwordNotice, setPasswordNotice] = useState(""); const [changing, setChanging] = useState(false);
  // 访客口令是注册时生成的随机值,本人并不知道,给入口只会让人点了必然失败
  const canChangePassword = user.role !== "访客";
  const resetPasswordForm = () => { setCurrentPassword(""); setNewPassword(""); setRepeatPassword(""); setPasswordError(""); setPasswordNotice(""); };
  const changePassword = async () => {
    if (newPassword.length < 8) { setPasswordError("新密码至少 8 位"); return; }
    if (newPassword !== repeatPassword) { setPasswordError("两次输入的新密码不一致"); return; }
    setChanging(true); setPasswordError(""); setPasswordNotice("");
    try {
      await api("/api/auth/password", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }) });
      resetPasswordForm();
      setPasswordNotice("密码已修改，其他设备需要重新登录");
    } catch (e) { setPasswordError((e as Error).message); } finally { setChanging(false); }
  };
  useEffect(() => { if (open) { setName(user.display_name); setPreview(user.avatar_data || ""); setAvatar(null); setError(""); resetPasswordForm(); } }, [open, user]);
  useEffect(() => { if (!avatar) return; const url = URL.createObjectURL(avatar); setPreview(url); return () => URL.revokeObjectURL(url); }, [avatar]);
  if (!open) return null;
  const save = async () => { setSaving(true); setError(""); try { const form = new FormData(); form.append("display_name", name); if (avatar) form.append("avatar", avatar); const updated = await api<User>("/api/profile", { method: "PUT", body: form }); onSaved(updated); onClose(); } catch (e) { setError((e as Error).message); } finally { setSaving(false); } };
  return <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}><section className="profile-modal" role="dialog" aria-modal="true" aria-labelledby="profile-title"><header><div><span>账户设置</span><h2 id="profile-title">个人信息</h2></div><button className="icon" aria-label="关闭个人信息" onClick={onClose}><X /></button></header><div className="profile-body"><label className="avatar-editor">{preview ? <img src={preview} alt="头像预览" /> : <span>{name.slice(0, 1) || "管"}</span>}<i><Camera /></i><input aria-label="选择新头像" type="file" accept="image/png,image/jpeg,image/webp" onChange={e => setAvatar(e.target.files?.[0] || null)} /></label><div className="profile-fields"><label>显示名称<input value={name} maxLength={30} onChange={e => setName(e.target.value)} /></label><label>登录账号<input value={user.username} disabled /></label><p>名称与头像保存在筑听数据库中，刷新或重新登录后仍会保留。</p></div></div>
    {canChangePassword && <div className="password-section">
      <h3>修改密码</h3>
      <div className="profile-fields">
        <label>当前密码<input type="password" autoComplete="current-password" value={currentPassword} maxLength={128} onChange={e => setCurrentPassword(e.target.value)} /></label>
        <label>新密码<input type="password" autoComplete="new-password" value={newPassword} maxLength={128} placeholder="至少 8 位" onChange={e => setNewPassword(e.target.value)} /></label>
        <label>确认新密码<input type="password" autoComplete="new-password" value={repeatPassword} maxLength={128} onChange={e => setRepeatPassword(e.target.value)} /></label>
        <p>修改成功后，其他设备上的登录会立即失效，本机无需重新登录。</p>
      </div>
      {passwordError && <div className="profile-error" role="alert">{passwordError}</div>}
      {passwordNotice && <div className="profile-notice" role="status">{passwordNotice}</div>}
      <button className="secondary" disabled={changing || !currentPassword || !newPassword || !repeatPassword} onClick={changePassword}>{changing ? "修改中..." : "修改密码"}</button>
    </div>}
    {error && <div className="profile-error" role="alert">{error}</div>}<footer><button className="secondary" onClick={onClose}>取消</button><button className="primary" disabled={saving} onClick={save}>{saving ? "保存中..." : "保存修改"}</button></footer></section></div>;
}

function SceneTabs({ scene, view, setScene, openTemplates }: { scene: Scene; view: View; setScene: (scene: Scene) => void; openTemplates: () => void }) {
  return <div className="scene-tabs" role="tablist" aria-label="会议场景">{scenes.map(item => <button role="tab" aria-selected={view === "workspace" && scene === item.key} tabIndex={view === "workspace" && scene === item.key ? 0 : -1} key={item.key} onClick={() => setScene(item.key)}>{item.label}</button>)}<button role="tab" aria-selected={view === "templates"} tabIndex={view === "templates" ? 0 : -1} onClick={openTemplates}>自定义模板</button></div>;
}

function Inspector({ meeting, stage, progress }: { meeting?: Meeting; stage: Stage; progress: number }) {
  const activeIndex = workflow.findIndex(item => item.key === stage);
  return <aside className="inspector">
    <section><h3>会议信息</h3><dl><div><dt>会议主题</dt><dd>{meeting?.title || "等待生成"}</dd></div><div><dt>更新时间</dt><dd>{meeting ? new Date(meeting.created_at * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "刚刚"}</dd></div><div><dt>输入来源</dt><dd>{meeting?.source || "录音输入"}</dd></div><div><dt>会议标签</dt><dd>{meeting?.mode_label || "智能识别"}</dd></div></dl></section>
    <section><div className="progress-head"><h3>当前进度</h3><span>{progress}%</span></div>
      <div className="progress-track"><i style={{ width: `${Math.min(100, Math.max(progress, 4))}%` }} /></div>
      <div className="workflow-list">{workflow.map((item, index) => {
        const finished = progress >= 100;
        const state = finished || index < activeIndex ? "done" : index === activeIndex ? "active" : "pending";
        const showHint = finished ? index === workflow.length - 1 : state === "active";
        return <div key={item.key} className={`workflow-step ${state}`}>
          <span className="step-node">{state === "done" ? <Check /> : null}</span>
          <span className="step-label">{item.label}</span>
          {showHint && <span className={`step-hint ${finished ? "ok" : ""}`}>{finished ? "已完成" : "进行中"}</span>}
        </div>;
      })}</div></section>
  </aside>;
}

function segmentTranscript(text: string): string {
  return text
    .replace(/\r\n?/g, "\n")
    .replace(/[。！？!?；;]+\s*/g, match => `${match.trim()}\n`)
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function previewTranscript(text: string): string {
  const segmented = segmentTranscript(text);
  if (segmented.length <= MAX_LIVE_PREVIEW_CHARS) return segmented;
  const tail = segmented.slice(-MAX_LIVE_PREVIEW_CHARS);
  const firstCompleteLine = tail.indexOf("\n");
  return `…${firstCompleteLine >= 0 ? tail.slice(firstCompleteLine + 1) : tail}`;
}

function transcriptLines(text: string): string[] {
  return segmentTranscript(text)
    .split(/\n+/)
    .map(line => line.trim())
    .filter(Boolean);
}

function formatTranscriptTime(seconds?: number): string {
  if (seconds === undefined) return "--:--:--";
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remain = total % 60;
  return [hours, minutes, remain].map(value => String(value).padStart(2, "0")).join(":");
}

function recordingMimeType(): string {
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
  return candidates.find(type => typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(type)) || "";
}

function DurableRecorderPanel({
  scene,
  ownerUsername,
  onCompleted,
  onLiveTranscript,
  onRecordingStateChange,
  setStage,
  setProgress,
  onProcessingChange,
  viewingArchived,
  onStartNew,
  initialMode,
  sessionKey,
  onManageTemplates,
}: RecorderPanelProps) {
  const [mode, setMode] = useState<"device" | "upload" | "live">(initialMode);
  // finalizeCapture 是异步的:它的闭包捕获的是发起录音那一刻的 mode。用 ref
  // 读当前值,避免录音期间切了输入方式后仍按旧模式判断。
  // 处理方式与模板只在开始录音前可改:录音一旦开始,这一场用的就是当时的设置,
  // 中途改了却不生效比改不了更容易误导人。录音保存与随后的自动处理期间同样锁住。
  const modeRef = useRef(initialMode);
  useEffect(() => { modeRef.current = mode; }, [mode]);
  const [file, setFile] = useState<File | null>(null);
  const [processMode, setProcessMode] = useState("生成纪要，暂不推送");
  const [status, setStatus] = useState("");
  const [recording, setRecording] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [processing, setProcessing] = useState(false);
  useEffect(() => { onProcessingChange(processing); }, [processing, onProcessingChange]);
  const [elapsed, setElapsed] = useState(0);
  const [templates, setTemplates] = useState<CustomTemplate[]>([]);
  const [templateId, setTemplateId] = useState("");
  const compatibleTemplates = useMemo(
    () => templates.filter(item => item.scenario === "auto" || (scene !== "auto" && item.scenario === scene)),
    [scene, templates],
  );
  const [recoverableDraft, setRecoverableDraft] = useState<RecordingDraftMeta>();
  const [recovering, setRecovering] = useState(false);
  const [draftStorageNotice, setDraftStorageNotice] = useState(false);
  const active = useRef<ActiveRecording | undefined>(undefined);
  const currentDraftId = useRef<string | undefined>(undefined);
  const mounted = useRef(true);
  const speech = useRef<RealtimeAsrHandle | null>(null);
  const jobPoll = useRef<number | undefined>(undefined);
  const timer = useRef<number | undefined>(undefined);
  const templateChoiceTouched = useRef(false);
  const fullTranscript = useRef("");
  const committedTranscript = useRef("");

  const publishTranscript = (capture: ActiveRecording | undefined, value: string, interim = "") => {
    if (capture && active.current !== capture) return;
    const segmented = segmentTranscript(value);
    fullTranscript.current = segmented;
    if (capture) capture.transcript = segmented;
    const rows = capture ? capture.transcriptRows.slice(-MAX_LIVE_TRANSCRIPT_ROWS) : [];
    const pending = interim.trim();
    if (capture && pending) {
      rows.push({
        id: `${capture.id}-interim`,
        text: pending,
        elapsedSeconds: Math.floor(calculateElapsed(capture)),
        interim: true,
      });
    }
    onLiveTranscript(previewTranscript(segmented), rows);
  };

  const calculateElapsed = (capture: ActiveRecording) => {
    capture.elapsedSeconds = Math.max(0, (Date.now() - capture.startedAtEpoch) / 1000);
    return capture.elapsedSeconds;
  };

  const updateDraftMeta = (capture: ActiveRecording, statusValue: RecordingDraftMeta["status"] = "recording") => {
    if (!capture.persistenceEnabled) return;
    if (capture.metaTimer) window.clearTimeout(capture.metaTimer);
    capture.metaTimer = window.setTimeout(() => {
      void updateRecordingDraft(capture.id, {
        elapsedSeconds: calculateElapsed(capture),
        transcript: capture.transcript,
        status: statusValue,
        chunkCount: capture.persistedChunkCount,
      }).catch(() => {
        capture.persistenceEnabled = false;
        if (mounted.current && active.current === capture) setDraftStorageNotice(true);
      });
    }, RECORDING_DRAFT_META_DELAY);
  };

  const stopSpeech = () => {
    const session = speech.current;
    speech.current = null;
    // stop() 内部会摘掉 onclose 再关，不会触发重连；它同时负责关掉计费上行。
    if (session) session.stop();
  };

  const finalizeCapture = (capture: ActiveRecording): Promise<File | null> => {
    if (capture.finalizePromise) return capture.finalizePromise;
    capture.finalizePromise = (async () => {
      await capture.writeChain;
      calculateElapsed(capture);
      let persisted = new Blob([], { type: capture.mimeType || "audio/webm" });
      if (capture.persistedChunkCount > 0) {
        try {
          persisted = (await readRecordingDraft(capture.id)).blob;
        } catch {
          if (capture.persistenceEnabled) capture.persistenceEnabled = false;
        }
      }
      const pieces = persisted.size > 0 ? [persisted, ...capture.fallbackChunks] : capture.fallbackChunks;
      const blob = pieces.length ? new Blob(pieces, { type: capture.mimeType || "audio/webm" }) : null;
      const result = blob && blob.size > 0
        ? new File([blob], capture.filename, { type: capture.mimeType || blob.type || "audio/webm" })
        : null;
      if (capture.persistenceEnabled) {
        await updateRecordingDraft(capture.id, {
          elapsedSeconds: capture.elapsedSeconds,
          transcript: capture.transcript,
          status: "stopped",
          chunkCount: capture.chunkIndex,
        }).catch(() => {});
      }
      if (capture.metaTimer) window.clearTimeout(capture.metaTimer);
      capture.stream.getTracks().forEach(track => track.stop());
      capture.finalized = true;
      if (active.current === capture) active.current = undefined;
      if (mounted.current) {
        setFile(result);
        setElapsed(capture.elapsedSeconds);
        setRecording(false);
        setFinalizing(false);
        setStatus(result ? "录音已保存，正在生成会议纪要..." : "没有采集到有效音频");
        onRecordingStateChange(false);
        // 实时录音停下来就该出纪要 —— 用户的动作是「开会、结束」,不是「结束、
        // 再点一下处理」。上传/设备读取仍需手动触发:那两种模式下选完文件还要
        // 调处理方式,自动开跑会打断设置。
        if (result && capture.stoppedByUser && modeRef.current === "live") void submit(result);
      }
      return result;
    })().catch(error => {
      if (mounted.current) {
        setFinalizing(false);
        setRecording(false);
        setStatus(`录音保存失败：${(error as Error).message}`);
        onRecordingStateChange(false);
      }
      return null;
    });
    return capture.finalizePromise;
  };

  const appendChunk = (capture: ActiveRecording, chunk: Blob) => {
    const index = capture.chunkIndex;
    capture.chunkIndex += 1;
    if (!capture.persistenceEnabled) {
      capture.fallbackChunks.push(chunk);
      return;
    }
    capture.writeChain = capture.writeChain.then(async () => {
      if (!capture.persistenceEnabled) {
        capture.fallbackChunks.push(chunk);
        return;
      }
      try {
        await appendRecordingChunk(capture.id, index, chunk);
        capture.persistedChunkCount = index + 1;
        try {
          await updateRecordingDraft(capture.id, {
            elapsedSeconds: calculateElapsed(capture),
            transcript: capture.transcript,
            chunkCount: capture.persistedChunkCount,
            status: "recording",
          });
        } catch {
          if (mounted.current && active.current === capture) setDraftStorageNotice(true);
        }
      } catch {
        capture.persistenceEnabled = false;
        capture.fallbackChunks.push(chunk);
        if (mounted.current && active.current === capture) setDraftStorageNotice(true);
      }
    }).catch(() => {
      capture.persistenceEnabled = false;
      capture.fallbackChunks.push(chunk);
    });
  };

  /** 接上实时转写。
   *
   * 走的是我们自己的 ASR（与会议字幕同一条链路、同一份工程热词），而不是浏览器
   * 自带的 Web Speech API —— 后者拿不到热词表，「监理」「旁站」「检验批」这类
   * 现场术语几乎必错。注意这只影响录制过程中看到的文字：录完上传后仍会由整段
   * 识别重新转一遍，那才是纪要的权威文本。
   */
  const attachSpeech = (capture: ActiveRecording) => {
    // 一句话在说完之前会反复重发（interim），说完才定稿（final）。按 sentence_id
    // 记住当前这句，才能让预览里的半句被下一次更新替换掉而不是越堆越长。
    let interim = "";
    const commit = (value: string) => {
      const text = value.trim();
      if (!text) return;
      const elapsedSeconds = Math.floor(calculateElapsed(capture));
      transcriptLines(text).forEach(line => {
        capture.transcriptRows.push({
          id: `${capture.id}-${capture.transcriptSequence}`,
          text: line,
          elapsedSeconds,
        });
        capture.transcriptSequence += 1;
      });
      if (capture.transcriptRows.length > MAX_LIVE_TRANSCRIPT_ROWS) {
        capture.transcriptRows.splice(0, capture.transcriptRows.length - MAX_LIVE_TRANSCRIPT_ROWS);
      }
      const previous = committedTranscript.current.trimEnd();
      const separator = previous && !/[\n。！？!?；;]$/.test(previous) ? "\n" : "";
      committedTranscript.current = `${previous}${separator}${text}`;
    };
    speech.current = startRealtimeAsr({
      stream: capture.stream,
      // 录音可能被浏览器单方面终止(麦克风被系统回收),那条路上没人会调 stopSpeech,
      // 少了这道闸重连就会一直空转。
      shouldContinue: () => !capture.stopping && active.current === capture && mounted.current,
      onStatus: message => {
        if (mounted.current && active.current === capture) setStatus(message);
      },
      onMessage: item => {
        if (active.current !== capture) return;
        if (item.type === "asr.error") {
          if (mounted.current) setStatus("实时转写暂时中断，录音仍在继续");
          return;
        }
        if (item.type === "transcript.final") {
          commit(item.text || "");
          interim = "";
        } else if (item.type === "transcript.interim") {
          interim = item.text || "";
        } else return;
        const committed = committedTranscript.current.trimEnd();
        const preview = committed && interim.trim() ? `${committed}\n${interim.trim()}` : `${committed}${interim}`;
        publishTranscript(capture, preview, interim);
        updateDraftMeta(capture);
      },
    });
  };

  const startRecording = async () => {
    if (active.current || finalizing) return;
    if (!navigator.mediaDevices?.getUserMedia) {
      setStatus("当前浏览器不支持麦克风录音");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      const mimeType = recordingMimeType();
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      const id = typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `recording-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const capture: ActiveRecording = {
        id,
        filename: `实时录音_${Date.now()}.webm`,
        mimeType: recorder.mimeType || mimeType || "audio/webm",
        stream,
        recorder,
        generation: sessionKey,
        startedAtEpoch: Date.now(),
        elapsedSeconds: 0,
        chunkIndex: 0,
        persistedChunkCount: 0,
        fallbackChunks: [],
        writeChain: Promise.resolve(),
        persistenceEnabled: true,
        transcript: "",
        transcriptRows: [],
        transcriptSequence: 0,
      };
      try {
        // 新录音落库前先清掉更早的草稿:它们既不会出现在「恢复录音」里,又占着
        // 浏览器配额;配额占满会让分片写入开始失败,正好破坏这套保护机制本身。
        await pruneRecordingDrafts(ownerUsername, capture.id).catch(() => undefined);
        await createRecordingDraft({
          id: capture.id,
          ownerUsername,
          filename: capture.filename,
          mimeType: capture.mimeType,
          startedAt: capture.startedAtEpoch,
          elapsedSeconds: 0,
          transcript: "",
          status: "recording",
          chunkCount: 0,
          updatedAt: Date.now(),
        });
      } catch {
        capture.persistenceEnabled = false;
        setDraftStorageNotice(true);
      }
      recorder.ondataavailable = event => { if (event.data?.size) appendChunk(capture, event.data); };
      recorder.onerror = () => { if (mounted.current && active.current === capture) setStatus("录音设备出现异常，正在保存已采集内容"); };
      recorder.onstop = () => { void finalizeCapture(capture); };
      active.current = capture;
      currentDraftId.current = capture.id;
      fullTranscript.current = "";
      committedTranscript.current = "";
      onLiveTranscript("", []);
      setFile(null);
      setRecording(true);
      setFinalizing(false);
      setElapsed(0);
      setStatus("实时录音中");
      onRecordingStateChange(true);
      recorder.start(1000);
      timer.current = window.setInterval(() => {
        const current = active.current;
        if (!current) return;
        setElapsed(calculateElapsed(current));
      }, 250);
      attachSpeech(capture);
    } catch (error) {
      setStatus(`无法开始录音：${(error as Error).message}`);
    }
  };

  const stopRecording = () => {
    const capture = active.current;
    if (!capture || capture.stopping) return;
    capture.stopping = true;
    capture.stoppedByUser = true;
    setFinalizing(true);
    setStatus("正在保存录音分片...");
    stopSpeech();
    if (timer.current) { window.clearInterval(timer.current); timer.current = undefined; }
    if (capture.recorder.state === "inactive") void finalizeCapture(capture);
    else capture.recorder.stop();
  };

  const recoverDraft = async () => {
    if (!recoverableDraft) return;
    setRecovering(true);
    try {
      const recovered = await readRecordingDraft(recoverableDraft.id);
      const recoveredFile = new File([recovered.blob], recovered.meta.filename, { type: recovered.meta.mimeType || recovered.blob.type || "audio/webm" });
      currentDraftId.current = recovered.meta.id;
      fullTranscript.current = segmentTranscript(recovered.meta.transcript || "");
      onLiveTranscript(previewTranscript(fullTranscript.current), []);
      setFile(recoveredFile);
      setElapsed(recovered.meta.elapsedSeconds || 0);
      setMode("live");
      setRecoverableDraft(undefined);
      // 恢复出来就直接处理。实时录音下已经没有「开始处理」按钮了(停止录音即
      // 自动生成),不在这里接上的话,恢复出来的录音会卡在面板里永远提交不了。
      // 草稿之所以存在,恰恰就是因为它还没被处理过。
      setStatus("已恢复上次未提交的录音，正在生成会议纪要...");
      void submit(recoveredFile);
    } catch (error) {
      setStatus(`恢复录音失败：${(error as Error).message}`);
    } finally {
      setRecovering(false);
    }
  };

  const discardDraft = async () => {
    if (!recoverableDraft) return;
    await deleteRecordingDraft(recoverableDraft.id).catch(() => {});
    setRecoverableDraft(undefined);
    setStatus("未提交录音已清理");
  };

  // overrideFile:录音刚保存完就自动提交时,setFile 引起的重渲染还没发生,
  // 闭包里的 file 仍是旧值(null),必须把文件直接传进来。
  const submit = async (overrideFile?: File | null) => {
    // 只有手动点「开始处理」才需要这道守卫。自动提交是在录音保存完成的回调里
    // 发起的,那时录音已经结束 —— 而 setFinalizing(false) 是异步的,闭包里读到的
    // finalizing 很可能还是 true,照着它判断会把自动提交整个挡掉,「停止录音就
    // 出纪要」就永远不会发生。传了文件即代表来自那条路径。
    if (!overrideFile && (recording || finalizing)) { setStatus("请先停止录音，等待录音保存完成"); return; }
    const transcript = fullTranscript.current.trim();
    const audio = overrideFile ?? file;
    if (!audio && !transcript) { setStatus("请先上传或录制音频"); return; }
    const form = new FormData();
    if (audio) form.append("audio", audio);
    form.append("live_text", transcript);
    // 显式告知来源。服务端原先靠「有没有实时字幕」反推,而字幕依赖
    // Web Speech API —— iOS Safari 基本不支持,实时录音会被记成「上传音频」;
    // 反过来先录音再改上传文件,残留字幕又会让上传文件被记成「实时录音」。
    form.append("input_mode", mode);
    form.append("process_mode", processMode);
    form.append("app_mode", scene);
    if (templateId) form.append("custom_template_id", templateId);
    setStatus("录音已进入处理队列"); setStage("input"); setProgress(5); setProcessing(true);
    try {
      const { job_id } = await api<{ job_id: string }>("/api/process", { method: "POST", body: form });
      const startedAt = Date.now(); let attempts = 0; let failures = 0;
      const poll = async () => {
        if (document.hidden) { jobPoll.current = window.setTimeout(poll, 10_000); return; }
        try {
          const job = await api<Job>(`/api/jobs/${job_id}`); attempts += 1; failures = 0; setStage(job.stage); setProgress(job.progress); setStatus(job.message);
          if (job.status === "completed" && job.result) {
            setProcessing(false);
            const draftId = currentDraftId.current;
            if (draftId) { await deleteRecordingDraft(draftId).catch(() => {}); currentDraftId.current = undefined; }
            onCompleted({ id: job.result.record_id, title: "新会议", created_at: Date.now() / 1000, mode: (job.result.mode || scene) as Scene, mode_label: job.result.mode_label || "智能识别", source: mode === "live" ? "实时录音" : mode === "upload" ? "上传音频" : "筑听读取", transcript: job.result.transcript || transcript, summary: job.result.summary || "", overview: job.result.overview || "", status: job.result.status || "报告已生成", custom_template_id: job.result.custom_template_id, custom_template_name: job.result.custom_template_name });
            return;
          }
          if (job.status === "failed") { setProcessing(false); setStatus(job.error || "处理失败"); return; }
          if (Date.now() - startedAt > 2 * 60 * 60 * 1000) { setProcessing(false); setStatus("处理时间超出预期，请在历史会议中查看结果"); return; }
          const delay = attempts < 20 ? 1_500 : attempts < 80 ? 3_000 : 8_000;
          jobPoll.current = window.setTimeout(poll, delay);
        } catch (error) {
          failures += 1;
          if (failures <= 5) { setStatus("连接波动，正在重试查询处理进度..."); jobPoll.current = window.setTimeout(poll, 4_000); return; }
          setProcessing(false); setStatus(`${(error as Error).message}；任务仍在后台处理，稍后可在历史会议中查看结果`);
        }
      };
      void poll();
    } catch (error) { setProcessing(false); setStatus((error as Error).message); }
  };

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; if (jobPoll.current) window.clearTimeout(jobPoll.current); if (timer.current) window.clearInterval(timer.current); const capture = active.current; if (capture && !capture.stopping) { capture.stopping = true; stopSpeech(); if (capture.recorder.state === "inactive") void finalizeCapture(capture); else capture.recorder.stop(); } }; }, []);
  useEffect(() => {
    const capture = active.current;
    if (capture && !capture.stopping) { capture.stopping = true; stopSpeech(); if (capture.recorder.state === "inactive") void finalizeCapture(capture); else capture.recorder.stop(); }
    setMode(initialMode); setFile(null); setElapsed(0); setRecording(false); setFinalizing(false); setStatus(""); fullTranscript.current = ""; committedTranscript.current = ""; onLiveTranscript("", []); onRecordingStateChange(false);
  }, [sessionKey, initialMode]);
  useEffect(() => { void getLatestRecordingDraft(ownerUsername).then(draft => { if (draft && draft.chunkCount > 0) setRecoverableDraft(draft); }).catch(() => {}); }, [ownerUsername]);
  useEffect(() => { api<{ items: CustomTemplate[] }>("/api/custom-templates").then(data => setTemplates(data.items)).catch(error => setStatus((error as Error).message)); }, []);
  useEffect(() => {
    const selectionStillValid = !templateId || compatibleTemplates.some(item => item.id === templateId);
    if (!selectionStillValid) templateChoiceTouched.current = false;
    if (templateChoiceTouched.current && selectionStillValid) return;
    const preferred = compatibleTemplates.find(item => item.is_default);
    setTemplateId(preferred?.id || "");
  }, [compatibleTemplates, templateId]);

  // 录音一开始,这一场用的处理方式与模板就固定了 —— 中途改了却不生效,
  // 比改不了更容易误导人。录音保存与随后的自动处理期间同样锁住。
  const settingsLocked = recording || finalizing || processing;

  // 看一场已经完成的会议时,「筑听读取 / 上传音频 / 实时录音 + 处理模式」整套输入
  // 都不该出现:它们属于「新建一场纪要」,摆在这里既是噪音,也真的会出事 ——
  // 打开历史记录并不会重置录音面板(sessionKey 不变),上一轮残留的文件或实时
  // 字幕还在,这时点「开始处理」提交的是那份残留素材,而用户正看着另一场会。
  if (viewingArchived) {
    return <section className="recorder-panel panel recorder-panel--archived">
      <div className="panel-heading"><div><h2>会议纪要</h2></div><FileText /></div>
      <div className="archived-note">
        <div className="archived-icon"><Archive /></div>
        <h3>正在查看已完成的会议</h3>
        <p>右侧是这场会议的概览、纪要与原始转写,可直接导出或推送。</p>
        <button className="primary" onClick={onStartNew}><Mic />开始新录音</button>
      </div>
    </section>;
  }

  return <section className="recorder-panel panel">
    <div className="panel-heading"><div><h2>创建录音纪要</h2></div><AudioLines /></div>
    <div className="input-tabs"><button disabled={recording || finalizing} className={mode === "device" ? "active" : ""} onClick={() => setMode("device")}><FileAudio />筑听读取</button><button disabled={recording || finalizing} className={mode === "upload" ? "active" : ""} onClick={() => setMode("upload")}><Upload />上传音频</button><button disabled={recording || finalizing} className={mode === "live" ? "active" : ""} onClick={() => setMode("live")}><Mic />实时录音</button></div>
    <div className="input-body">
      {mode === "device" && <div className="device-state"><div className="device-icon"><RefreshCw /></div><h3>筑听设备自动读取</h3><p>插入设备后自动发现最新录音，也可以手动选择录音文件。</p><label className="secondary file-button"><FileAudio />选择设备录音<input type="file" accept="audio/*" onChange={event => setFile(event.target.files?.[0] || null)} /></label></div>}
      {mode === "upload" && <label className="dropzone"><Upload /><b>{file?.name || "将音频拖放到此处"}</b><span>支持 WAV、MP3、M4A、AAC、FLAC、OGG、WebM</span><input type="file" accept="audio/*" onChange={event => setFile(event.target.files?.[0] || null)} /></label>}
      {mode === "live" && <div className="live-recorder"><div className={`record-orbit ${recording ? "recording" : ""}`}><Mic /></div><h3>{recording ? "正在聆听" : file ? "录音已完成" : "浏览器实时录音"}</h3><div className="timer">{String(Math.floor(elapsed / 60)).padStart(2, "0")}:{String(Math.floor(elapsed % 60)).padStart(2, "0")}</div><div className="record-actions">{!recording ? <button className="primary" disabled={finalizing} onClick={startRecording}><Play />开始录音</button> : <button className="danger" onClick={stopRecording}><Square />停止录音</button>}</div>{recoverableDraft && !file && <div className="recording-recovery" role="status">
      <button className="recovery-dismiss" aria-label="清理未提交录音" onClick={discardDraft}><X /></button>
      <b>检测到未提交录音</b>
      <span>{formatDraftLength(recoverableDraft.elapsedSeconds)}，恢复后将自动生成纪要</span>
      <button className="secondary" disabled={recovering} onClick={recoverDraft}>{recovering ? "恢复中..." : "恢复录音"}</button>
    </div>}</div>}
    </div>
    <div className="template-controls"><label><span>纪要模板</span><select disabled={settingsLocked} value={templateId} onChange={event => { templateChoiceTouched.current = true; setTemplateId(event.target.value); }}><option value="">系统自动匹配</option>{compatibleTemplates.map(item => <option key={item.id} value={item.id}>{item.name}{item.is_default ? "（默认）" : ""}</option>)}</select></label><button className="secondary template-manage-button" disabled={settingsLocked} onClick={onManageTemplates}><LayoutTemplate />管理模板</button></div>
    <div className={`process-controls ${mode === "live" ? "no-action" : ""}`}><label><span>处理模式</span><select disabled={settingsLocked} value={processMode} onChange={event => setProcessMode(event.target.value)}><option>生成纪要，暂不推送</option><option>生成并推送钉钉</option><option>只转写，不推送</option></select></label>{mode !== "live" && <button className="primary process-button" disabled={recording || finalizing} onClick={() => void submit()}><Sparkles />开始处理</button>}</div>
    {draftStorageNotice && <div className="status-line recording-storage-warning" role="status"><span className="status-dot" />浏览器本地恢复存储不可用，当前录音仍会保存到本页，结束前请勿关闭页面。</div>}
    {status && <div className="status-line" role="status"><span className="status-dot" />{status}</div>}
  </section>;
}

function resultHeading(meeting?: Meeting): string {
  if (meeting?.mode === "personal") return "个人备忘录";
  const label = meeting?.mode_label?.trim();
  return !label || label === "智能识别" ? "会议记录" : label;
}

function ResultPanel({ meeting, onUpdate, liveTranscript = "", liveTranscriptRows = [], liveRecording = false, speakerName = "当前用户", generating = false, stage = "input", progress = 0 }: { meeting?: Meeting; onUpdate: (m: Meeting) => void; liveTranscript?: string; liveTranscriptRows?: LiveTranscriptRow[]; liveRecording?: boolean; speakerName?: string; generating?: boolean; stage?: Stage; progress?: number }) {
  const [tab, setTab] = useState<"overview" | "summary" | "review" | "transcript">("overview"); const [review, setReview] = useState(""); const [reviewMode, setReviewMode] = useState<Scene>("general"); const [notice, setNotice] = useState("");
  const transcriptScrollRef = useRef<HTMLElement | null>(null);
  useEffect(() => setReview(meeting?.summary || ""), [meeting?.id, meeting?.summary]);
  useEffect(() => setReviewMode(normalizeScene(meeting?.mode)), [meeting?.id, meeting?.mode]);
  // 录音开始切到「原始转写」看实时字幕;录音结束再切回「会议概览」——
  // 那里正是纪要生成进度的位置,停在转写页会让人以为停止后什么都没发生。
  // 只在「本来就停在转写页」时切回:录音期间用户若自己翻到别的页,是刻意的,
  // 不该被拽走。
  const wasLiveRecording = useRef(false);
  useEffect(() => {
    if (liveRecording) { wasLiveRecording.current = true; setTab("transcript"); return; }
    if (!wasLiveRecording.current) return;
    wasLiveRecording.current = false;
    setTab(current => (current === "transcript" ? "overview" : current));
  }, [liveRecording]);
  useEffect(() => {
    if (!liveRecording || tab !== "transcript" || !transcriptScrollRef.current) return;
    transcriptScrollRef.current.scrollTop = transcriptScrollRef.current.scrollHeight;
  }, [liveRecording, liveTranscript, liveTranscriptRows, tab]);
  const saveReview = async () => { if (!meeting) return; const updated = await api<Meeting>(`/api/history/${meeting.id}/review`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ summary: review, mode: reviewMode }) }); onUpdate(updated); setNotice("校核稿与场景确认已保存"); };
  const exportFile = async (kind: "docx" | "pdf") => {
    if (!meeting) return;
    const createdAt = new Date(meeting.created_at * 1000);
    const response = await fetch(`/api/export/${kind}`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        summary: review || meeting.summary,
        overview: meeting.overview,
        mode: meeting.mode,
        mode_label: meeting.mode_label,
        title: meeting.title,
        transcript: meeting.transcript,
        date: createdAt.toLocaleDateString("zh-CN"),
        start_time: createdAt.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
        custom_template_id: meeting.custom_template_id || "",
      }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      return setNotice(body.detail || "导出失败");
    }
    const blob = await response.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `筑听_${meeting.title}.${kind}`;
    link.click();
    URL.revokeObjectURL(link.href);
  };
  const push = async () => { if (!meeting) return; const data = await api<{ status: string }>("/api/dingtalk/push", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ summary: review || meeting.summary, mode: meeting.mode }) }); setNotice(data.status); };
  const transcript = liveTranscript || meeting?.transcript || "";
  const bodyText = tab === "overview" ? meeting?.overview || "" : tab === "summary" ? meeting?.summary || "" : transcript;
  const showLiveTranscript = tab === "transcript" && liveRecording;
  const transcriptRows = tab === "transcript" ? transcriptLines(bodyText) : [];
  const timelineRows: LiveTranscriptRow[] = liveTranscriptRows.length ? liveTranscriptRows : transcriptRows.map((text, index) => ({ id: `fallback-${index}`, text }));
  const emptyTitle = tab === "transcript" ? "暂无转写内容" : tab === "summary" ? "暂无结构化纪要" : "暂无会议概览";
  // 没有纪要时的原因说明。此前一律写死「该会议以“只转写，不推送”处理」——
  // 而「没人说话」和「纪要生成失败」也会走到这个分支,等于对用户断言了一个
  // 不存在的原因,两种情况在界面上还长得一模一样,分不清是自己没说话还是系统出错。
  // 依据 minutes_status(机器可判)而不是后端那句中文文案:比对文字改一个字就失效。
  const noMinutesReason = meeting?.minutes_status === "empty"
    ? "本次会议没有检测到有效发言，未生成纪要。"
    : meeting?.minutes_status === "failed"
      ? "纪要生成失败，可查看原始转写；稍后可在录音纪要中重新处理。"
      : "该会议以“只转写，不推送”处理，未生成此内容；可在录音纪要中重新处理以生成。";
  return <section className="result-panel panel"><div className="result-heading"><div><h2>{resultHeading(meeting)}</h2></div><FileText /></div>
    {meeting && !meeting.draft && <Suspense fallback={null}><SoundSpecimen seed={meeting.id} scene={meeting.mode as never} duration={Math.max(600, (meeting.transcript || "").length * 0.6)} label={meeting.mode_label} caption={`${meeting.id.replace(/^meeting-/, "NO.")} · ${new Date(meeting.created_at * 1000).toLocaleDateString("zh-CN")}`} /></Suspense>}
    <div className="result-tabs" role="tablist" aria-label="会议结果">{[["overview", "会议概览"], ["summary", "结构化纪要"], ["review", "人工校核"], ["transcript", "原始转写"]].map(([key, label]) => <button role="tab" aria-selected={tab === key} tabIndex={tab === key ? 0 : -1} key={key} className={tab === key ? "active" : ""} onClick={() => setTab(key as typeof tab)}>{label}</button>)}</div>
    <div className="result-body">{tab === "review" && meeting && !meeting.draft ? <div className="review-workspace"><label className="review-scene-control"><span>人工确认场景</span><select disabled={meeting.read_only} value={reviewMode} onChange={event => setReviewMode(event.target.value as Scene)}>{scenes.slice(1).map(item => <option key={item.key} value={item.key}>{item.label}</option>)}</select><small>确认或纠正结果会作为可审计反馈，用于持续校准智能识别。</small></label><textarea className="review-editor" value={review} onChange={e => setReview(e.target.value)}
      /* 参会者能看不能改。不置只读的话,他能一路敲字却找不到保存按钮,写完全丢。 */
      readOnly={meeting.read_only} title={meeting.read_only ? "你以参会者身份查看这场会议，校核稿由会议创建者定稿" : undefined} /></div> : bodyText.trim() ? tab === "transcript" ? showLiveTranscript ? <article ref={transcriptScrollRef} className="recording-caption-timeline" role="log" aria-live="polite" aria-relevant="additions text" aria-label="实时录音转写">{timelineRows.map(row => <div className={`recording-caption-row ${row.interim ? "interim" : ""}`} key={row.id}><time>{formatTranscriptTime(row.elapsedSeconds)}</time><p><b>{speakerName}：</b><span>{row.text}</span></p></div>)}</article> : <article ref={transcriptScrollRef} className="document-view transcript-document" role="log">{transcriptRows.map((line, index) => <div className="transcript-line" key={`${index}-${line.slice(0, 32)}`}><p>{line}</p></div>)}</article> : <article className={`document-view ${tab === "overview" ? "overview-document" : ""}`}>{bodyText}</article> : generating ? <div className="empty-result generating-result" role="status" aria-live="polite"><div className="generating-spinner" aria-hidden="true" /><h3>正在生成会议纪要…</h3><p>{workflow.find(item => item.key === stage)?.label || "处理中"}</p><div className="generating-track"><i style={{ width: `${Math.min(100, Math.max(progress, 4))}%` }} /></div><span className="generating-percent">{Math.min(100, Math.max(progress, 0))}%</span></div> : <div className="empty-result"><FileText /><h3>{emptyTitle}</h3><p>{showLiveTranscript ? "开始录音后，实时转写会按句显示在这里。" : tab === "transcript" ? "这场会议没有保存原始转写文本。" : !meeting || meeting.draft ? "完成录音后，筑听将自动识别场景并套用对应模板。" : noMinutesReason}</p></div>}</div>
    {meeting && !meeting.draft && <div className="result-actions"><button className="secondary" onClick={() => exportFile("docx")}><Download />Word</button><button className="secondary" onClick={() => exportFile("pdf")}><Download />PDF</button>{tab === "review" && !meeting.read_only && <button className="primary" onClick={saveReview}><Check />保存校核稿</button>}{!meeting.read_only && <button className="primary" onClick={push}><Send />推送钉钉</button>}</div>}{notice && <div className="notice">{notice}</div>}
  </section>;
}

function HistoryView({ meetings, query, setQuery, onSelect, onNew, openingId }: { meetings: Meeting[]; query: string; setQuery: (value: string) => void; onSelect: (id: string) => void; onNew: () => void; openingId: string }) {
  const groups = useMemo(() => scenes.slice(1).map(scene => ({ label: scene.label, count: meetings.filter(item => item.mode === scene.key).length })), [meetings]);
  return <section className="history-view"><header className="history-hero"><div><h1>历史会议</h1></div><button className="primary" onClick={onNew}><Mic />开始新录音</button></header><div className="history-stats"><div><span>会议总数</span><b>{meetings.length}</b></div>{groups.map(group => <div key={group.label}><span>{group.label}</span><b>{group.count}</b></div>)}</div><div className="history-toolbar"><div className="search-box"><Search /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索标题、摘要、待办或场景" /></div><span>{meetings.length} 条结果</span></div><div className="history-grid">{meetings.map(item => <button key={item.id} disabled={Boolean(openingId)} aria-busy={openingId === item.id} onClick={() => onSelect(item.id)}><span className="history-scene">{item.mode_label}</span><h3>{item.title}</h3><p>{item.overview || item.summary || "暂无会议概览"}</p><footer><span>{new Date(item.created_at * 1000).toLocaleString("zh-CN")}</span><span>{openingId === item.id ? "正在打开…" : "打开会议"}</span></footer></button>)}{!meetings.length && <div className="history-empty"><Archive /><h3>没有匹配的历史会议</h3><p>调整搜索词，或开始一场新录音。</p></div>}</div></section>;
}

function Assistant({ open, close, meeting }: { open: boolean; close: () => void; meeting?: Meeting }) {
  const [messages, setMessages] = useState<ChatMessage[]>([{ role: "assistant", content: "你好，我是筑听（开源版）。我的会议理解与业务问答能力由你本地部署的开源模型提供，所有数据只保存在本机。你可以问我平台操作、当前会议内容，也可以检索历史会议。" }]); const [question, setQuestion] = useState(""); const [loading, setLoading] = useState(false);
  const ask = async (preset?: string) => { const text = (preset ?? question).trim(); if (!text || loading) return; const context = messages.slice(-8); setMessages(v => [...v, { role: "user", content: text }]); setQuestion(""); setLoading(true); try { const data = await api<{ answer: string }>("/api/assistant", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: text, record_id: meeting?.draft ? undefined : meeting?.id, overview: meeting?.overview, summary: meeting?.summary, transcript: meeting?.transcript, history: context }) }); setMessages(v => [...v, { role: "assistant", content: data.answer }]); } catch (e) { setMessages(v => [...v, { role: "assistant", content: (e as Error).message }]); } finally { setLoading(false); } };
  return <div className={`assistant-drawer ${open ? "open" : ""}`} role="dialog" aria-modal="false" aria-label="问筑听" aria-hidden={!open}><header><div><span className="online-dot" /><b>筑听 · 平台助手</b></div><button className="icon" aria-label="关闭问筑听" onClick={close}><X /></button></header><div className="chat-body" aria-live="polite">{messages.map((message, index) => <div key={index} className={`chat-message ${message.role}`}>{message.content}</div>)}{loading && <div className="typing">筑听正在分析...</div>}</div><div className="quick-questions">{["提取当前会议待办", "总结核心结论", "总结本周会议", "怎么导出 Word"].map(item => <button key={item} onClick={() => ask(item)}>{item}</button>)}</div><div className="chat-compose"><input aria-label="向筑听提问" value={question} onChange={e => setQuestion(e.target.value)} onKeyDown={e => { if (e.key === "Enter") ask(); }} placeholder="问筑听平台或会议相关的问题..." /><button aria-label="发送问题" disabled={loading || !question.trim()} onClick={() => ask()}><Send /></button></div></div>;
}

export default function App() {
  const [user, setUser] = useState<User | null>(null); const [loading, setLoading] = useState(true); const [scene, setScene] = useState<Scene>("auto"); const [meetings, setMeetings] = useState<Meeting[]>([]); const [recentMeetings, setRecentMeetings] = useState<Meeting[]>([]); const [meeting, setMeeting] = useState<Meeting>(); const [stage, setStage] = useState<Stage>("input"); const [progress, setProgress] = useState(0); const [query, setQuery] = useState(""); const [assistant, setAssistant] = useState(false); const [sidebar, setSidebar] = useState(false); const [profile, setProfile] = useState(false); const [view, setView] = useState<View>(readView); const [inputMode, setInputMode] = useState<"device" | "upload" | "live">("live"); const [sessionKey, setSessionKey] = useState(0); const [meetingSession, setMeetingSession] = useState<JoinResponse>(); const [openingId, setOpeningId] = useState(""); const [skin, setSkin] = useState<Skin>(readSkin); const [liveTranscript, setLiveTranscript] = useState(""); const [liveTranscriptRows, setLiveTranscriptRows] = useState<LiveTranscriptRow[]>([]); const [liveRecording, setLiveRecording] = useState(false); const [jobRunning, setJobRunning] = useState(false);
  useEffect(() => { applySkin(skin); localStorage.setItem(SKIN_KEY, skin); }, [skin]);
  useEffect(() => { sessionStorage.setItem(VIEW_KEY, view); }, [view]);
  const toggleSkin = () => setSkin(current => (current === "classic" ? "atlas" : "classic"));
  const updateLiveTranscript = (text: string, rows: LiveTranscriptRow[] = []) => {
    setLiveTranscript(text);
    setLiveTranscriptRows(rows);
  };
  const loadHistory = async (q = "") => {
    const data = await api<{ items: Meeting[] }>(`/api/history?q=${encodeURIComponent(q)}`);
    const normalized = data.items.map(normalizeMeeting);
    setMeetings(normalized);
    if (!q) setRecentMeetings(normalized);
  };
  useEffect(() => {
    const restore = async () => {
      const currentUser = await api<User>("/api/auth/me");
      setUser(currentUser);
      await loadHistory();
      const active = JSON.parse(sessionStorage.getItem(ACTIVE_MEETING_KEY) || "null") as { username?: string; meetingId?: string } | null;
      if (active?.username === currentUser.username && active.meetingId) {
        try {
          const restored = await api<JoinResponse>(`/api/meetings/${encodeURIComponent(active.meetingId)}/join`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ display_name: currentUser.display_name }),
          });
          setMeetingSession(restored);
          setView("meetings");
          return;
        } catch {
          sessionStorage.removeItem(ACTIVE_MEETING_KEY);
        }
      }
      const workspace = JSON.parse(sessionStorage.getItem(WORKSPACE_KEY) || "null") as {
        username?: string; meeting?: Meeting; scene?: Scene; stage?: Stage; progress?: number; inputMode?: "device" | "upload" | "live";
      } | null;
      if (workspace?.username === currentUser.username && workspace.meeting) {
        setMeeting(workspace.meeting);
        setScene(normalizeScene(workspace.scene || workspace.meeting.mode));
        setStage(workspace.stage || "input");
        setProgress(workspace.progress || 0);
        setInputMode(workspace.inputMode || "live");
      } else if (workspace?.username && workspace.username !== currentUser.username) {
        sessionStorage.removeItem(WORKSPACE_KEY);
        sessionStorage.removeItem(ACTIVE_MEETING_KEY);
        setView("workspace");
      }
    };
    restore().catch(() => setUser(null)).finally(() => setLoading(false));
  }, []);
  useEffect(() => {
    if (!user || !meeting) return;
    sessionStorage.setItem(WORKSPACE_KEY, JSON.stringify({
      username: user.username, meeting, scene, stage, progress, inputMode,
    }));
  }, [user, meeting, scene, stage, progress, inputMode]);
  useEffect(() => {
    const expire = () => {
      sessionStorage.removeItem(ACTIVE_MEETING_KEY);
      setMeetingSession(undefined); setProfile(false); setAssistant(false); setUser(null);
    };
    window.addEventListener("jkinco:auth-expired", expire);
    return () => window.removeEventListener("jkinco:auth-expired", expire);
  }, []);
  useEffect(() => { if (user && new URLSearchParams(location.search).get("meeting")) setView("meetings"); }, [user]);
  useEffect(() => { if (!user) return; const timer = window.setTimeout(() => loadHistory(query).catch(() => {}), 250); return () => clearTimeout(timer); }, [query, user]);
  // 必须放在所有提前返回(loading / !user / meetingSession)之前:hooks 的调用
  // 数量在每次渲染间必须一致,放在提前返回之后会在命中那些分支时少调一个,
  // React 直接抛 #310 白屏 —— 类型检查和单元测试都发现不了,只有真跑才暴露。
  // 实时会议的纪要由服务端异步生成,打开记录那一刻往往还没好。不轮询的话
  // 界面会一直停在「纪要生成中」,用户以为卡死了,只能自己刷新。
  useEffect(() => {
    const realtimeId = meeting?.realtime_meeting_id;
    if (!realtimeId || meeting?.minutes_status !== "processing") return;
    let cancelled = false;
    let timer = 0;
    let attempts = 0;
    const tick = async () => {
      // 标签页在后台时放慢:长会议的纪要要跑好几分钟,固定 4 秒会在用户切走之后
      // 继续每分钟打十几次。
      if (document.hidden) { timer = window.setTimeout(tick, 15_000); return; }
      attempts += 1;
      try {
        const next = normalizeMeeting(await api<Meeting>(`/api/meetings/${encodeURIComponent(realtimeId)}/record`));
        if (cancelled) return;
        if (next.minutes_status !== "processing") { setMeeting(next); loadHistory(); return; }
      } catch {
        // 轮询失败不打断界面:下一次再试,生成本身在服务端照常进行
      }
      if (cancelled) return;
      // 逐步退避。服务端对卡住的生成有超时复位(见 recover_stuck_minutes),
      // 所以这里不必设硬上限 —— 状态迟早会离开 processing,轮询自然结束。
      timer = window.setTimeout(tick, attempts < 15 ? 4_000 : attempts < 40 ? 10_000 : 30_000);
    };
    timer = window.setTimeout(tick, 4_000);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [meeting?.realtime_meeting_id, meeting?.minutes_status]);
  // 慢网络下点击卡片到数据返回之间必须有反馈,否则用户会以为点击失效而重复点击
  const selectMeeting = async (id: string) => {
    if (openingId) return;
    setOpeningId(id);
    try {
      const data = normalizeMeeting(await api<Meeting>(`/api/history/${id}`));
      setMeeting(data); setLiveTranscript(""); setLiveTranscriptRows([]); setLiveRecording(false);
      setScene(data.mode); setStage(data.summary ? "push" : "review"); setProgress(data.summary ? 100 : 75); setView("workspace"); setSidebar(false);
    } finally { setOpeningId(""); }
  };
  const openRealtimeRecord = async (id: string) => { const data = normalizeMeeting(await api<Meeting>(`/api/meetings/${encodeURIComponent(id)}/record`)); setMeeting(data); setLiveTranscript(""); setLiveTranscriptRows([]); setLiveRecording(false); setScene(data.mode); setStage(data.summary ? "push" : "review"); setProgress(data.summary ? 100 : 75); setView("workspace"); setSidebar(false); };
  const completed = (item: Meeting) => { const normalized = normalizeScene(item.mode); setMeeting({ ...item, mode: normalized }); setLiveRecording(false); setLiveTranscript(""); setLiveTranscriptRows([]); setScene(normalized); setProgress(100); setStage("push"); loadHistory(); };
  const logout = async () => {
    await api("/api/auth/logout", { method: "POST" });
    sessionStorage.removeItem(ACTIVE_MEETING_KEY);
    sessionStorage.removeItem(WORKSPACE_KEY);
    sessionStorage.removeItem(VIEW_KEY);
    setUser(null);
  };
  const newMeeting = (mode: "device" | "upload" | "live") => { const now = Date.now(); setLiveTranscript(""); setLiveTranscriptRows([]); setLiveRecording(false); setView("workspace"); setInputMode(mode); setScene("auto"); setStage("input"); setProgress(0); setMeeting({ id: `draft-${now}`, title: "未命名会议", created_at: now / 1000, mode: "auto", mode_label: "智能识别", source: mode === "live" ? "实时录音" : mode === "upload" ? "上传音频" : "筑听读取", transcript: "", summary: "", overview: "", status: "等待录音输入", draft: true }); setSessionKey(value => value + 1); setSidebar(false); };
  const chooseScene = (next: Scene) => { setScene(next); setView("workspace"); setSidebar(false); };
  const currentScene = scenes.find(item => item.key === scene)!;
  if (loading) return <div className="boot-screen"><AudioLines /><span>正在启动筑听平台</span></div>;
  if (!user) return <Login onLogin={u => { setUser(u); loadHistory(); }} />;
  if (meetingSession) return <Suspense fallback={<div className="boot-screen"><Video /><span>正在进入会议</span></div>}><MeetingRoom session={meetingSession} user={user} onLeave={() => {
    sessionStorage.removeItem(ACTIVE_MEETING_KEY);
    setMeetingSession(undefined); setView("meetings"); loadHistory();
  }} /></Suspense>;
  // 两条产出纪要的路径都要在结果区显示「生成中」:
  //   录音纪要 —— 停止录音后自动提交,任务进度在 stage/progress 上;
  //   实时会议 —— 结束会议后由服务端异步生成,状态写在会议记录上。
  // 只看其中一条,另一条就会显得像卡住了。
  // jobRunning 由录音面板上报,不再从进度反推(失败时进度会停在中途)。
  // 实时会议:结束后服务端异步生成纪要,minutes_status 是机器可判的状态字段
  // (status 是给人看的中文文案,拿它比对改一个字就会失效)。
  const meetingMinutesPending = meeting?.minutes_status === "processing";
  // draft=true 是「正准备录一场新的」;没有它就说明当前展示的是一场已存在的会议。
  // 录音/处理进行中不算 —— 那时面板正在被使用,不能把它换掉。
  const viewingArchived = Boolean(meeting && !meeting.draft) && !liveRecording && !jobRunning;


  return <div className="app-shell">
    <div className={`sidebar-scrim ${sidebar ? "show" : ""}`} onClick={() => setSidebar(false)} />
    <Sidebar composing={view === "workspace" && !viewingArchived} meetings={recentMeetings} selected={meeting?.draft ? undefined : meeting?.id} onSelect={selectMeeting} collapsed={!sidebar} onClose={() => setSidebar(false)} view={view} onMeetings={() => { setView("meetings"); setSidebar(false); }} onMinutes={() => newMeeting("live")} onHistory={() => { setQuery(""); setView("history"); setSidebar(false); }} />
    <div className={`app-main app-main--${view} ${view === "workspace" ? "with-inspector" : "wide"}`}><Header user={user} query={query} setQuery={setQuery} onLogout={logout} openSidebar={() => setSidebar(true)} onProfile={() => setProfile(true)} skin={skin} onToggleSkin={toggleSkin} /><main className={`content content--${view}`}>{view === "history" ? <HistoryView meetings={meetings} query={query} setQuery={setQuery} onSelect={selectMeeting} onNew={() => newMeeting("live")} openingId={openingId} /> : view === "meetings" ? <Suspense fallback={<div className="boot-screen"><Video /><span>正在加载实时会议</span></div>}><MeetingLobby user={user} onJoin={data => {
      sessionStorage.setItem(ACTIVE_MEETING_KEY, JSON.stringify({ username: user.username, meetingId: data.meeting.id }));
      setMeetingSession(data);
    }} onBack={() => setView("workspace")} onOpenRecord={openRealtimeRecord} /></Suspense> : <><SceneTabs scene={scene} view={view} setScene={chooseScene} openTemplates={() => { setView("templates"); setSidebar(false); }} />{view === "templates" ? <TemplateCenter /> : <><div className="scene-intro"><div><h1>{currentScene.label}</h1><p>{currentScene.short}</p></div></div><div className="workspace"><DurableRecorderPanel scene={scene} ownerUsername={user.username} onCompleted={completed} onLiveTranscript={updateLiveTranscript} onRecordingStateChange={setLiveRecording} setStage={setStage} setProgress={setProgress} onProcessingChange={setJobRunning} viewingArchived={viewingArchived} onStartNew={() => newMeeting("live")} initialMode={inputMode} sessionKey={sessionKey} onManageTemplates={() => setView("templates")} /><ResultPanel generating={jobRunning || meetingMinutesPending} stage={stage} progress={progress} meeting={meeting} liveTranscript={liveTranscript} liveTranscriptRows={liveTranscriptRows} liveRecording={liveRecording} speakerName={user.display_name || user.username} onUpdate={m => { setMeeting(m); loadHistory(); }} /></div></>}</>}</main></div>
    {view === "workspace" && <Inspector meeting={meeting} stage={stage} progress={progress} />}
    <button className="assistant-ball" aria-label="打开问筑听" onClick={() => setAssistant(true)}><Bot />问筑听</button><Assistant open={assistant} close={() => setAssistant(false)} meeting={meeting?.draft ? undefined : meeting} />
    <ProfileModal user={user} open={profile} onClose={() => setProfile(false)} onSaved={setUser} />
    <AppFooter />
  </div>;
}
