import { useEffect, useMemo, useRef, useState } from "react";
import {
  LiveKitRoom, RoomAudioRenderer, VideoConference, useLocalParticipant, useRoomContext, useTracks,
} from "@livekit/components-react";
import "@livekit/components-styles";
import { DisconnectReason, Track } from "livekit-client";
import {
  CalendarClock, Captions, ChevronLeft, Clock3, Copy, Link2, Lock,
  LogOut, Maximize2, MessageSquare, Mic, MicOff, Minimize2, MonitorUp, MoreHorizontal, Plus, Radio, Search, ShieldCheck, Users, Video, VideoOff, X,
} from "lucide-react";
import { resampleToPcm16 } from "./realtimeAsr";

type User = { username: string; display_name: string; role: string; avatar_data?: string };
export type RealtimeMeeting = {
  id: string; meeting_code: string; room_name: string; title: string; creator_username: string;
  host_username: string; status: string; is_locked: boolean; realtime_transcription_enabled: boolean;
  auto_minutes_enabled: boolean; auto_record: boolean; actual_start_at?: number; ended_at?: number;
  scheduled_start_at?: number; scheduled_end_at?: number; duration_seconds: number; minutes_status: string; created_at: number;
  recurrence?: string;
  history_record_id?: string;
};
export type JoinResponse = {
  meeting: RealtimeMeeting; token: string; identity: string; role: "host" | "participant";
  livekit_url: string; asr_enabled: boolean; preview_mode?: boolean;
};
type CaptionItem = {
  sentence_id: number; text: string; type: string; start_time_ms: number;
  participant_identity?: string; speaker_name?: string; speaker_username?: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { credentials: "include", ...init });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (response.status === 401) {
      window.dispatchEvent(new CustomEvent("jkinco:auth-expired"));
    }
    throw new Error(body.detail || `请求失败 (${response.status})`);
  }
  return response.json();
}

/** 会议号位数,与后端 create_meeting 生成的 NNN-NNN-NNN 一致 */
const MEETING_CODE_DIGITS = 9;

/**
 * 把任意输入规整成 NNN-NNN-NNN。
 * 只保留数字再按 3-3-3 分组:因此纯数字、带横杠、带空格、粘贴时混入的
 * 全角符号或不可见字符都能正确落位,也不会出现重复横杠。
 */
function formatMeetingCode(raw: string): string {
  const digits = (raw || "").replace(/\D/g, "").slice(0, MEETING_CODE_DIGITS);
  return digits.replace(/(\d{3})(?=\d)/g, "$1-");
}

/**
 * 格式化后重新计算光标位置。
 *
 * 直接把 value 换成格式化结果会让光标跳到末尾 —— 在中间插入或退格时尤其难用。
 * 做法是数出光标前有多少个**数字**,再在新串里找到第 n 个数字之后的位置:
 * 横杠是自动补的,不参与计数,因此插入、删除、中间编辑都能落在预期位置。
 */
function meetingCodeCaret(formatted: string, digitsBeforeCaret: number): number {
  if (digitsBeforeCaret <= 0) return 0;
  let seen = 0;
  for (let index = 0; index < formatted.length; index += 1) {
    if (/\d/.test(formatted[index])) {
      seen += 1;
      if (seen === digitsBeforeCaret) return index + 1;
    }
  }
  return formatted.length;
}

function formatTime(seconds: number) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remain = seconds % 60;
  return [hours, minutes, remain].map(value => String(value).padStart(2, "0")).join(":");
}

function defaultScheduleValue() {
  const date = new Date(Date.now() + 60 * 60 * 1000);
  date.setMinutes(Math.ceil(date.getMinutes() / 15) * 15, 0, 0);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function formatInvitationTime(timestamp: number) {
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(new Date(timestamp));
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find(part => part.type === type)?.value || "";
  return `${value("year")}/${value("month")}/${value("day")} ${value("hour")}:${value("minute")}`;
}

function invitationText(meeting: RealtimeMeeting, user: User) {
  const url = `${location.origin}/?meeting=${meeting.meeting_code}`;
  const startsAt = (meeting.scheduled_start_at || meeting.actual_start_at || meeting.created_at) * 1000;
  const endsAt = meeting.ended_at
    ? meeting.ended_at * 1000
    : meeting.scheduled_end_at
      ? meeting.scheduled_end_at * 1000
      : startsAt + Math.max(meeting.duration_seconds || 3600, 3600) * 1000;
  const start = formatInvitationTime(startsAt);
  const end = formatInvitationTime(endsAt);
  const endLabel = start.slice(0, 10) === end.slice(0, 10) ? end.slice(11) : end;
  return `筑听 ${user.display_name} 邀请您参加筑听会议
会议主题：${meeting.title}
会议时间：${start}-${endLabel} (GMT+08:00) 中国标准时间 - 北京

点击链接直接加入会议：
${url}

#筑听会议：${meeting.meeting_code}

复制该信息，打开浏览器即可参与`;
}

async function copyInvitation(meeting: RealtimeMeeting, user: User) {
  const invitation = invitationText(meeting, user);
  if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(invitation);
  else {
    const input = document.createElement("textarea"); input.value = invitation; input.style.position = "fixed"; input.style.opacity = "0";
    document.body.appendChild(input); input.select(); document.execCommand("copy"); input.remove();
  }
}

/** 微信内置浏览器对 WebRTC 的支持有缺陷,需要单独的兼容处理 */
const isWeChatBrowser = () => /MicroMessenger/i.test(navigator.userAgent);

/**
 * adaptiveStream 让 LiveKit 按画面实际尺寸/可见性自动请求低分辨率层,
 * 是大会带宽能收住的关键(100 人会议里没有它,每个端都会收全分辨率流)。
 * 微信内置浏览器上它会导致共享画面停帧,因此**只对微信关闭**,不要全局关。
 * dynacast 让发布端停掉无人订阅的层,进一步省上行。
 */
const ROOM_OPTIONS = {
  adaptiveStream: !isWeChatBrowser(),
  dynacast: true,
};

/** 微信共享画面重挂载的次数上限,超过后不再重建,避免整场会议持续闪烁 */
const MAX_SHARED_RECOVERY = 3;

function clearMeetingInviteFromUrl() {
  const url = new URL(window.location.href);
  if (!url.searchParams.has("meeting")) return;
  url.searchParams.delete("meeting");
  window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
}

// 单场会议时长上限(分钟),与后端 JKINCO_MAX_SCHEDULED_DURATION 保持一致。
// 上限的意义:结束时间之前不回收空房,过长等于把回收永久关掉。
const MAX_MEETING_MINUTES = 6 * 60;

// 重复频率。周会这类固定会议原先只能每次重新预约,链接每周都变、要重新分发一遍。
// 选定频率后,同一场会议在结束时把预约时间滚到下一次 —— 会议号与链接始终不变。
const RECURRENCE_OPTIONS = [
  { value: "none", label: "不重复" },
  { value: "daily", label: "每天" },
  { value: "weekly", label: "每周" },
  { value: "biweekly", label: "每两周" },
] as const;

/** 在 datetime-local 的本地时间字符串上加减分钟,返回同样格式的字符串。 */
function shiftLocalDateTime(value: string, minutes: number): string {
  const base = new Date(value);
  if (Number.isNaN(base.getTime())) return value;
  const shifted = new Date(base.getTime() + minutes * 60_000);
  return new Date(shifted.getTime() - shifted.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

/** Unix 秒转 datetime-local 使用的本地时间字符串。 */
function timestampToLocalDateTime(timestamp?: number): string {
  if (!timestamp) return "";
  const date = new Date(timestamp * 1000);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

function CreateMeetingDialog({ open, mode, close, onCreated }: { open: boolean; mode: "instant" | "scheduled"; close: () => void; onCreated: (meeting: RealtimeMeeting) => void }) {
  const [title, setTitle] = useState(mode === "scheduled" ? "预约会议" : "即时会议");
  const [scheduledAt, setScheduledAt] = useState(defaultScheduleValue);
  // 结束时间:在此之前空房不会被自动回收。默认给一小时,上限 6 小时(后端同样校验)。
  const [scheduledEndAt, setScheduledEndAt] = useState(() => shiftLocalDateTime(defaultScheduleValue(), 60));
  const [recurrence, setRecurrence] = useState("none");
  const [transcription, setTranscription] = useState(true);
  const [minutes, setMinutes] = useState(true);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!open) return;
    setTitle(mode === "scheduled" ? "预约会议" : "即时会议");
    setRecurrence("none");
    if (mode === "scheduled") {
      const start = defaultScheduleValue();
      setScheduledAt(start);
      setScheduledEndAt(shiftLocalDateTime(start, 60));
    }
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") close(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [open, mode, close]);
  if (!open) return null;
  const create = async () => {
    setLoading(true); setError("");
    try {
      const meeting = await request<RealtimeMeeting>("/api/meetings", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title, realtime_transcription_enabled: transcription, auto_minutes_enabled: minutes,
          scheduled_start_at: mode === "scheduled" ? new Date(scheduledAt).getTime() / 1000 : null,
          scheduled_end_at: mode === "scheduled" && scheduledEndAt ? new Date(scheduledEndAt).getTime() / 1000 : null,
          // 即时会议没有预约时刻,无从推算下一次,后端也会拒绝
          recurrence: mode === "scheduled" ? recurrence : "none",
        }),
      });
      onCreated(meeting);
    } catch (e) { setError((e as Error).message); } finally { setLoading(false); }
  };
  return <div className="meeting-dialog-backdrop" onMouseDown={event => event.target === event.currentTarget && close()}>
    <section className="meeting-dialog" role="dialog" aria-modal="true" aria-labelledby="create-meeting-title"><header><div><span>筑听实时会议</span><h2 id="create-meeting-title">{mode === "scheduled" ? "预约一场会议" : "开始一场新会议"}</h2></div><button className="icon" aria-label="关闭创建会议" onClick={close}><X /></button></header>
      <label className="meeting-field">会议主题<input value={title} maxLength={80} onChange={event => setTitle(event.target.value)} autoFocus /></label>
      {mode === "scheduled" && <label className="meeting-field schedule-field">开始时间<input type="datetime-local" value={scheduledAt} min={new Date(Date.now() - new Date().getTimezoneOffset() * 60_000).toISOString().slice(0, 16)} onChange={event => {
        const start = event.target.value;
        setScheduledAt(start);
        // 结束时间早于新的开始时间就没有意义了,顺延一小时;否则保留用户已选的值
        if (!scheduledEndAt || new Date(scheduledEndAt) <= new Date(start)) setScheduledEndAt(shiftLocalDateTime(start, 60));
      }} /></label>}
      {mode === "scheduled" && <label className="meeting-field schedule-field">结束时间<input type="datetime-local" value={scheduledEndAt} min={scheduledAt} max={shiftLocalDateTime(scheduledAt, MAX_MEETING_MINUTES)} onChange={event => setScheduledEndAt(event.target.value)} /></label>}
      {mode === "scheduled" && <label className="meeting-field schedule-field">重复频率<select value={recurrence} onChange={event => setRecurrence(event.target.value)}>{RECURRENCE_OPTIONS.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>}
      {mode === "scheduled" && recurrence !== "none" && <p className="meeting-hint">会议号与邀请链接保持不变，每次结束后自动排到下一次同一时间。</p>}
      <div className="meeting-options">
        <label><span><Captions /><b>实时转录</b><small>开源版暂未开放实时字幕</small></span><input type="checkbox" checked={transcription} onChange={event => setTranscription(event.target.checked)} /></label>
        <label><span><ShieldCheck /><b>自动纪要</b><small>结束后生成结构化筑听纪要</small></span><input type="checkbox" checked={minutes} onChange={event => setMinutes(event.target.checked)} /></label>
      </div>
      <p className="meeting-consent">进入会议即表示知悉：会议可能进行实时转录，转录内容仅用于生成会议纪要。</p>
      {error && <div className="form-error">{error}</div>}
      <footer><button className="secondary" onClick={close}>取消</button><button className="primary" disabled={loading || !title.trim() || (mode === "scheduled" && new Date(scheduledAt).getTime() < Date.now() + 60_000)} onClick={create}>{mode === "scheduled" ? <CalendarClock /> : <Video />}{loading ? "正在创建..." : mode === "scheduled" ? "完成预约" : "创建并进入"}</button></footer>
    </section>
  </div>;
}

function RescheduleMeetingDialog({
  meeting, close, onSaved,
}: {
  meeting: RealtimeMeeting | null;
  close: () => void;
  onSaved: (meeting: RealtimeMeeting) => void;
}) {
  const [scheduledAt, setScheduledAt] = useState("");
  const [scheduledEndAt, setScheduledEndAt] = useState("");
  const [scope, setScope] = useState<"occurrence" | "series">("occurrence");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const initializedMeetingId = useRef("");
  const scheduledAtInput = useRef<HTMLInputElement>(null);
  const scheduledEndAtInput = useRef<HTMLInputElement>(null);
  const recurring = Boolean(meeting?.recurrence && meeting.recurrence !== "none");
  const recurrenceLabel = RECURRENCE_OPTIONS.find(item => item.value === meeting?.recurrence)?.label || "当前频率";

  useEffect(() => {
    if (!meeting) {
      initializedMeetingId.current = "";
      return;
    }
    // 列表会定时拉取最新状态。只按会议 ID 初始化一次，避免轮询带来的新对象
    // 覆盖用户正在编辑的时间和改期范围。
    if (initializedMeetingId.current === meeting.id) return;
    initializedMeetingId.current = meeting.id;
    setScheduledAt(timestampToLocalDateTime(meeting.scheduled_start_at));
    setScheduledEndAt(timestampToLocalDateTime(meeting.scheduled_end_at));
    setScope("occurrence");
    setError("");
  }, [meeting]);

  useEffect(() => {
    if (!meeting) return;
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") close(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [meeting, close]);

  if (!meeting) return null;
  const save = async () => {
    // datetime-local controls can update their visible native value before a
    // framework change event is observed (notably in some embedded browsers).
    // Persist exactly what the user currently sees in the controls.
    const visibleScheduledAt = scheduledAtInput.current?.value || scheduledAt;
    const visibleScheduledEndAt = scheduledEndAtInput.current?.value || scheduledEndAt;
    setLoading(true); setError("");
    try {
      const updated = await request<RealtimeMeeting>(`/api/meetings/${meeting.id}/schedule`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scheduled_start_at: new Date(visibleScheduledAt).getTime() / 1000,
          scheduled_end_at: visibleScheduledEndAt ? new Date(visibleScheduledEndAt).getTime() / 1000 : null,
          scope: recurring ? scope : "occurrence",
        }),
      });
      onSaved(updated);
    } catch (e) { setError((e as Error).message); } finally { setLoading(false); }
  };
  const invalidStart = !scheduledAt || new Date(scheduledAt).getTime() < Date.now() + 60_000;
  const invalidEnd = Boolean(scheduledEndAt && new Date(scheduledEndAt) <= new Date(scheduledAt));
  return <div className="meeting-dialog-backdrop" onMouseDown={event => event.target === event.currentTarget && close()}>
    <section className="meeting-dialog reschedule-dialog" role="dialog" aria-modal="true" aria-labelledby="reschedule-meeting-title">
      <header><div><span>预约会议</span><h2 id="reschedule-meeting-title">修改预约时间</h2></div><button className="icon" aria-label="关闭修改预约" onClick={close}><X /></button></header>
      <div className="reschedule-meeting-summary"><b>{meeting.title}</b><span>会议号 {meeting.meeting_code}，邀请链接保持不变</span></div>
      <label className="meeting-field schedule-field">开始时间<input ref={scheduledAtInput} type="datetime-local" value={scheduledAt} min={new Date(Date.now() - new Date().getTimezoneOffset() * 60_000).toISOString().slice(0, 16)} onChange={event => {
        const start = event.target.value;
        const previousStart = scheduledAt;
        setScheduledAt(start);
        if (scheduledEndAt && previousStart) {
          const duration = new Date(scheduledEndAt).getTime() - new Date(previousStart).getTime();
          if (duration > 0) setScheduledEndAt(shiftLocalDateTime(start, Math.round(duration / 60_000)));
        }
      }} /></label>
      <label className="meeting-field schedule-field">结束时间（可选）<input ref={scheduledEndAtInput} type="datetime-local" value={scheduledEndAt} min={scheduledAt} max={scheduledAt ? shiftLocalDateTime(scheduledAt, MAX_MEETING_MINUTES) : undefined} onChange={event => setScheduledEndAt(event.target.value)} /></label>
      {recurring && <fieldset className="reschedule-scope"><legend>改期范围</legend>
        <label className={scope === "occurrence" ? "selected" : ""}><input type="radio" name="reschedule-scope" value="occurrence" checked={scope === "occurrence"} onChange={() => setScope("occurrence")} /><span><b>仅修改下一场</b><small>本场结束后，仍按原来的{recurrenceLabel}时间继续</small></span></label>
        <label className={scope === "series" ? "selected" : ""}><input type="radio" name="reschedule-scope" value="series" checked={scope === "series"} onChange={() => setScope("series")} /><span><b>本场及以后</b><small>从新时间开始，继续按{recurrenceLabel}重复</small></span></label>
      </fieldset>}
      {error && <div className="form-error">{error}</div>}
      <footer><button className="secondary" onClick={close}>取消</button><button className="primary" disabled={loading || invalidStart || invalidEnd} onClick={save}><CalendarClock />{loading ? "正在保存..." : "保存修改"}</button></footer>
    </section>
  </div>;
}

export function MeetingLobby({ user, onJoin, onBack, onOpenRecord }: { user: User; onJoin: (data: JoinResponse) => void; onBack: () => void; onOpenRecord: (meetingId: string) => Promise<void> }) {
  const [items, setItems] = useState<RealtimeMeeting[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [createMode, setCreateMode] = useState<"instant" | "scheduled">("instant");
  const [code, setCode] = useState("");
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [openingRecord, setOpeningRecord] = useState("");
  const [cancelling, setCancelling] = useState("");
  const [reschedulingMeeting, setReschedulingMeeting] = useState<RealtimeMeeting | null>(null);
  // 与后端 _require_host 同一口径:发起人、主持人、平台管理员。前端只是别显示
  // 点了必然 403 的按钮,真正的授权判定在服务端。
  const canManage = (meeting: RealtimeMeeting) =>
    user.username === meeting.creator_username || user.username === meeting.host_username || user.role === "平台管理员";
  const canCancel = (meeting: RealtimeMeeting) =>
    (meeting.status === "scheduled" || meeting.status === "active")
    && canManage(meeting);
  const cancelMeeting = async (meeting: RealtimeMeeting) => {
    if (!window.confirm(`确定取消「${meeting.title}」吗？已在会议中的成员会被移出，且不会生成纪要。`)) return;
    setCancelling(meeting.id); setError("");
    try {
      await request(`/api/meetings/${meeting.id}/cancel`, { method: "POST" });
      await load();
    } catch (e) { setError((e as Error).message); } finally { setCancelling(""); }
  };
  const [copied, setCopied] = useState("");
  const load = () => request<{ items: RealtimeMeeting[] }>("/api/meetings").then(data => setItems(data.items)).catch(() => undefined);
  useEffect(() => { void load(); }, []);
  const join = async (meeting: RealtimeMeeting | string) => {
    setError("");
    try {
      const id = typeof meeting === "string" ? meeting.trim() : meeting.id;
      if (!id) throw new Error("请输入会议号");
      const data = await request<JoinResponse>(`/api/meetings/${encodeURIComponent(id)}/join`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ display_name: user.display_name }),
      });
      onJoin(data);
    } catch (e) { setError((e as Error).message); }
  };
  // 依赖数组刻意为空:这是「从邀请链接自动入会」,只应在挂载时执行一次。
  // 把 join 加进依赖会让它每次渲染都重新入会。捕获初始闭包正是本意。
  useEffect(() => {
    const invited = new URLSearchParams(location.search).get("meeting");
    if (invited) void join(invited);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const filtered = items.filter(item => !query || item.title.toLowerCase().includes(query.toLowerCase()) || item.meeting_code.includes(query));
  const openRecord = async (meetingId: string) => {
    if (openingRecord) return;
    setError(""); setOpeningRecord(meetingId);
    try { await onOpenRecord(meetingId); }
    catch (e) { setError((e as Error).message); }
    finally { setOpeningRecord(""); }
  };
  const copyLink = async (meeting: RealtimeMeeting) => {
    setError("");
    try {
      await copyInvitation(meeting, user); setCopied(meeting.id);
      window.setTimeout(() => setCopied(current => current === meeting.id ? "" : current), 1800);
    } catch { setError("复制失败，请检查浏览器剪贴板权限"); }
  };
  const openCreate = (mode: "instant" | "scheduled") => { setCreateMode(mode); setCreateOpen(true); };
  return <main className="meeting-lobby">
    <header className="meeting-lobby-header"><button className="icon" aria-label="返回录音纪要" onClick={onBack}><ChevronLeft /></button><div><h1>实时会议</h1></div><div className="meeting-lobby-actions"><button className="secondary" title="预约会议" onClick={() => openCreate("scheduled")}><CalendarClock />预约会议</button><button className="primary" title="开始会议" onClick={() => openCreate("instant")}><Plus />开始会议</button></div></header>
    <section className="meeting-launch-band"><div><Radio /><span><b>发起或加入会议</b></span></div><div className="join-code"><input
        value={code}
        inputMode="numeric"
        autoComplete="off"
        maxLength={MEETING_CODE_DIGITS + 2}
        onChange={event => {
          const input = event.target;
          const caret = input.selectionStart ?? input.value.length;
          // 先数出光标之前有几个数字,格式化后据此还原光标,避免跳到末尾
          const digitsBeforeCaret = (input.value.slice(0, caret).match(/\d/g) || []).length;
          const formatted = formatMeetingCode(input.value);
          setCode(formatted);
          const nextCaret = meetingCodeCaret(formatted, digitsBeforeCaret);
          // 受控组件要等 React 写回 value 之后再设光标
          requestAnimationFrame(() => {
            if (document.activeElement === input) input.setSelectionRange(nextCaret, nextCaret);
          });
        }}
        onKeyDown={event => {
          // 退格时若光标正好在横杠后面,连同前一个数字一起删,否则会卡住删不动
          if (event.key !== "Backspace") return;
          const input = event.currentTarget;
          const caret = input.selectionStart ?? 0;
          if (caret !== input.selectionEnd || caret < 2 || input.value[caret - 1] !== "-") return;
          event.preventDefault();
          const next = formatMeetingCode(input.value.slice(0, caret - 2) + input.value.slice(caret));
          setCode(next);
          const digits = (input.value.slice(0, caret - 2).match(/\d/g) || []).length;
          const nextCaret = meetingCodeCaret(next, digits);
          requestAnimationFrame(() => input.setSelectionRange(nextCaret, nextCaret));
        }}
        placeholder="输入会议号"
      /><button onClick={() => join(code)}>加入会议</button></div></section>
    {error && <div className="meeting-error">{error}</div>}
    <div className="meeting-lobby-toolbar"><div className="search-box"><Search /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索会议名称或会议号" /></div><span>{filtered.length} 场会议</span></div>
    <section className="meeting-list"><header><h2>最近会议</h2></header><div className="meeting-list-grid">
      {filtered.map(meeting => <article key={meeting.id} className="meeting-list-card">
        <span className={`meeting-status ${meeting.status}`}>{meeting.status === "cancelled" ? "已取消" : meeting.status === "scheduled" ? "已预约" : meeting.status === "active" ? "进行中" : meeting.minutes_status === "processing" ? "纪要生成中" : "已结束"}</span>
        <h3>{meeting.title}</h3><p><Clock3 />{meeting.status === "scheduled" && meeting.scheduled_start_at ? `预约 ${new Date(meeting.scheduled_start_at * 1000).toLocaleString("zh-CN")}` : new Date(meeting.created_at * 1000).toLocaleString("zh-CN")}</p><p><Link2 />会议号 {meeting.meeting_code}</p>{meeting.recurrence && meeting.recurrence !== "none" && <p className="meeting-recurrence"><CalendarClock />{RECURRENCE_OPTIONS.find(item => item.value === meeting.recurrence)?.label || "重复"}</p>}
        <footer className={meeting.status === "scheduled" ? "scheduled-footer" : undefined}><span>{meeting.realtime_transcription_enabled ? "实时字幕已开启" : "未开启字幕"}</span><div className="meeting-card-actions"><button className="copy-link" onClick={() => void copyLink(meeting)}><Copy />{copied === meeting.id ? "已复制" : "复制邀请"}</button>{meeting.status === "scheduled" && canManage(meeting) && <button className="reschedule-meeting" onClick={() => setReschedulingMeeting(meeting)}><CalendarClock />修改预约</button>}<button className="open-meeting" disabled={openingRecord === meeting.id} onClick={() => meeting.status === "active" || meeting.status === "scheduled" ? void join(meeting) : void openRecord(meeting.id)}>{meeting.status === "scheduled" ? (Date.now() < (meeting.scheduled_start_at || 0) * 1000 ? "测试设备" : "开始会议") : meeting.status === "active" ? "进入会议" : openingRecord === meeting.id ? "正在打开..." : "查看记录"}</button>{canCancel(meeting) && <button className="cancel-meeting" disabled={cancelling === meeting.id} onClick={() => void cancelMeeting(meeting)}>{cancelling === meeting.id ? "取消中..." : "取消会议"}</button>}</div></footer>
      </article>)}
      {!filtered.length && <div className="meeting-empty"><CalendarClock /><h3>还没有实时会议</h3><p>点击右上角“开始会议”创建第一场会议。</p></div>}
    </div></section>
    <CreateMeetingDialog open={createOpen} mode={createMode} close={() => setCreateOpen(false)} onCreated={meeting => { setCreateOpen(false); void load(); if (meeting.status === "active") void join(meeting); else void copyLink(meeting); }} />
    <RescheduleMeetingDialog meeting={reschedulingMeeting} close={() => setReschedulingMeeting(null)} onSaved={meeting => { setItems(current => current.map(item => item.id === meeting.id ? meeting : item)); setReschedulingMeeting(null); void load(); }} />
  </main>;
}

function RealtimeAsr({ meeting, identity, enabled, onCaption }: { meeting: RealtimeMeeting; identity: string; enabled: boolean; onCaption: (item: CaptionItem) => void }) {
  const { localParticipant } = useLocalParticipant();
  const running = useRef(false);
  const microphoneTrack = localParticipant
    .getTrackPublication(Track.Source.Microphone)
    ?.track?.mediaStreamTrack;
  // 长会中实时字幕会话可能被服务端断开;自动重连保证字幕不永久中断
  const [attempt, setAttempt] = useState(0);
  const restarts = useRef(0);
  useEffect(() => {
    if (!enabled || !localParticipant.isMicrophoneEnabled || !microphoneTrack || running.current) return;
    let active = true;
    let socket: WebSocket | undefined;
    let context: AudioContext | undefined;
    let source: MediaStreamAudioSourceNode | undefined;
    let processor: ScriptProcessorNode | undefined;
    let silentOutput: GainNode | undefined;
    let restartTimer: number | undefined;
    running.current = true;
    const scheduleRestart = () => {
      if (!active || restarts.current >= 30) return;
      restarts.current += 1;
      restartTimer = window.setTimeout(() => setAttempt(value => value + 1), Math.min(3000 * restarts.current, 15_000));
    };
    const start = async () => {
      const protocol = location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${protocol}//${location.host}/api/realtime/asr/${meeting.id}?identity=${encodeURIComponent(identity)}`);
      socket.binaryType = "arraybuffer";
      socket.onmessage = event => {
        const item = JSON.parse(event.data);
        // 计数要等真的收到转写才清零,不能一连上就清。否则「连上→很快断→重连」
        // 这种循环里计数永远回不到上限,30 次的闸门形同虚设。服务端有空闲超时,
        // 音频图被浏览器挂起时每一轮都会连上再被关掉 —— 正是这个形状。
        if (item.type === "transcript.interim" || item.type === "transcript.final") {
          restarts.current = 0;
          onCaption(item);
        }
      };
      socket.onclose = () => { if (active) scheduleRestart(); };
      await new Promise<void>((resolve, reject) => { if (!socket) return reject(); socket.onopen = () => resolve(); socket.onerror = () => reject(new Error("实时转录连接失败")); });
      context = new AudioContext();
      await context.resume();
      source = context.createMediaStreamSource(new MediaStream([microphoneTrack]));
      processor = context.createScriptProcessor(4096, 1, 1);
      silentOutput = context.createGain();
      silentOutput.gain.value = 0;
      processor.onaudioprocess = event => {
        if (!active || socket?.readyState !== WebSocket.OPEN) return;
        const samples = event.inputBuffer.getChannelData(0);
        const pcm = resampleToPcm16(samples, event.inputBuffer.sampleRate);
        if (!pcm.length) return;
        socket.send(pcm.buffer as ArrayBuffer);
      };
      source.connect(processor);
      processor.connect(silentOutput);
      silentOutput.connect(context.destination);
    };
    start().catch(error => { onCaption({ sentence_id: -1, text: error.message, type: "asr.error", start_time_ms: 0 }); scheduleRestart(); });
    return () => {
      active = false; running.current = false;
      window.clearTimeout(restartTimer);
      if (socket) socket.onclose = null;
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "finish" }));
      socket?.close();
      processor?.disconnect();
      silentOutput?.disconnect();
      source?.disconnect();
      context?.close().catch(() => {});
      // microphoneTrack 由 LiveKit 拥有。这里只读它来发送 ASR PCM，绝不能 stop()，
      // 否则关闭字幕会连带切断会议中的麦克风。
    };
  }, [enabled, identity, localParticipant.isMicrophoneEnabled, meeting.id, microphoneTrack, onCaption, attempt]);
  return null;
}

function MeetingExperience({ session, user, leave }: { session: JoinResponse; user: User; leave: (ended?: boolean) => void }) {
  const { localParticipant } = useLocalParticipant();
  const room = useRoomContext();
  const sharedTracks = useTracks([{ source: Track.Source.ScreenShare, withPlaceholder: false }], { onlySubscribed: false });
  const [elapsed, setElapsed] = useState(Math.max(0, Math.floor(Date.now() / 1000 - (session.meeting.actual_start_at || Date.now() / 1000))));
  const [captions, setCaptions] = useState<CaptionItem[]>([]);
  // 窄屏进会议不默认展开转录:从 1100px 起侧栏就从右侧栏变成底部面板,一进来
  // 就盖掉大半个画面,而刚进会议时还没有人说话,那里必然是空的。宽屏侧栏在右边
  // 不挡画面,保持默认展开。只影响初始值,之后由用户自己开合。
  const [panel, setPanel] = useState<"captions" | "info" | "members" | "chat" | null>(
    () => (typeof window !== "undefined" && window.matchMedia("(max-width: 1100px)").matches ? null : "captions"),
  );
  const [participants, setParticipants] = useState<Array<{
    id: string; display_name: string; role: string; connection_status: string;
    livekit_identity?: string; username?: string;
  }>>([]);
  const [chat, setChat] = useState<Array<{ id: string; sender_name: string; message: string; created_at: number }>>([]);
  const [chatText, setChatText] = useState("");
  const [chatSending, setChatSending] = useState(false);
  const [chatError, setChatError] = useState("");
  const [ending, setEnding] = useState(false);
  const [meetingStatus, setMeetingStatus] = useState(session.meeting.status);
  const [fullscreen, setFullscreen] = useState(false);
  // 原生全屏不可用时(iOS Safari)的替代方案,见 enterTargetFullscreen
  const [softFullscreen, setSoftFullscreen] = useState(false);
  const [stageControlsVisible, setStageControlsVisible] = useState(true);
  // 手机端「更多」抽屉。桌面端这些按钮直接排在右下角,抽屉不参与布局。
  const [moreOpen, setMoreOpen] = useState(false);
  const [shareNotice, setShareNotice] = useState("");
  const [mediaError, setMediaError] = useState("");
  const [conferenceRevision, setConferenceRevision] = useState(0);
  const shellRef = useRef<HTMLDivElement>(null);
  const mountedSharedTrack = useRef("");
  const lastSharedRecovery = useRef(0);
  const sharedRecoveryCount = useRef(0);
  const stageControlTimer = useRef<number | undefined>(undefined);
  const lastFinalCaption = useRef({ text: "", at: 0 });
  const chatScrollRef = useRef<HTMLDivElement>(null);
  const captionScrollRef = useRef<HTMLDivElement>(null);
  const sharedTrackKey = sharedTracks
    .map(item => {
      if ("publication" in item && item.publication) {
        return `${item.publication.trackSid}-${item.publication.isSubscribed}`;
      }
      return item.participant.identity;
    })
    .join("|");
  useEffect(() => {
    if (!sharedTracks.length) return;
    const isWechat = /MicroMessenger/i.test(navigator.userAgent);
    const timers: number[] = [];
    const videoProgress = new WeakMap<HTMLVideoElement, { time: number; stalled: number }>();
    let frame = 0;
    let healthTimer = 0;
    const sharedVideos = () => shellRef.current?.querySelectorAll<HTMLVideoElement>(
      '[data-lk-source="screen_share"] video, video[data-lk-source="screen_share"]',
    ) || [];
    const refreshSharedVideo = () => {
      room.remoteParticipants.forEach(participant => {
        const publication = participant.getTrackPublication(Track.Source.ScreenShare);
        if (publication && !publication.isSubscribed) publication.setSubscribed(true);
      });
      window.dispatchEvent(new Event("resize"));
      sharedVideos().forEach(video => {
        video.muted = true;
        video.defaultMuted = true;
        video.playsInline = true;
        video.setAttribute("playsinline", "true");
        video.setAttribute("webkit-playsinline", "true");
        void video.play().catch(() => undefined);
      });
    };
    if (isWechat && mountedSharedTrack.current !== sharedTrackKey) {
      mountedSharedTrack.current = sharedTrackKey;
      setConferenceRevision(value => value + 1);
    }
    frame = window.requestAnimationFrame(() => {
      refreshSharedVideo();
      frame = window.requestAnimationFrame(refreshSharedVideo);
    });
    [120, 450, 900, 1600, 2800].forEach(delay => timers.push(window.setTimeout(refreshSharedVideo, delay)));
    if (isWechat) {
      healthTimer = window.setInterval(() => {
        let shouldRecover = false;
        sharedVideos().forEach(video => {
          const previous = videoProgress.get(video);
          const progressed = !previous || video.currentTime > previous.time + 0.05;
          const stalled = progressed ? 0 : previous.stalled + 1;
          videoProgress.set(video, { time: video.currentTime, stalled });
          if (video.paused || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA || stalled >= 2) {
            void video.play().catch(() => undefined);
          }
          if (stalled >= 3) shouldRecover = true;
        });
        // 重挂载会重建全部视频元素,代价很高。若共享流本身不可解码,
        // 无上限重试会让整个会场持续闪烁 —— 因此限次,超过后只保留温和的 play() 重试。
        if (
          shouldRecover
          && sharedRecoveryCount.current < MAX_SHARED_RECOVERY
          && Date.now() - lastSharedRecovery.current > 12_000
        ) {
          lastSharedRecovery.current = Date.now();
          sharedRecoveryCount.current += 1;
          setConferenceRevision(value => value + 1);
        }
      }, 2500);
    }
    document.addEventListener("WeixinJSBridgeReady", refreshSharedVideo);
    return () => {
      window.cancelAnimationFrame(frame);
      window.clearInterval(healthTimer);
      timers.forEach(timer => window.clearTimeout(timer));
      document.removeEventListener("WeixinJSBridgeReady", refreshSharedVideo);
    };
  }, [room, sharedTrackKey, sharedTracks.length]);
  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState === "hidden") return;
      window.requestAnimationFrame(() => {
        window.dispatchEvent(new Event("resize"));
        shellRef.current?.querySelectorAll<HTMLVideoElement>("video").forEach(video => void video.play().catch(() => undefined));
      });
    };
    window.addEventListener("pageshow", refresh);
    window.addEventListener("orientationchange", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.removeEventListener("pageshow", refresh);
      window.removeEventListener("orientationchange", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, []);
  useEffect(() => {
    const box = chatScrollRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [chat, panel]);
  useEffect(() => {
    const box = captionScrollRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [captions, panel]);
  useEffect(() => { const timer = window.setInterval(() => setElapsed(value => value + 1), 1000); return () => clearInterval(timer); }, []);
  useEffect(() => {
    const update = () => setFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", update);
    return () => document.removeEventListener("fullscreenchange", update);
  }, []);
  useEffect(() => () => window.clearTimeout(stageControlTimer.current), []);
  // 大会议室自动降低轮询频率:100 人 × 高频轮询会压垮单进程后端
  /**
   * 当前在线成员。
   *
   * 后端返回的是全量参会记录(含已离开的),直接渲染会把「张三 已离开」一直挂在
   * 列表里,人数也不准。这里只保留 connected,并按稳定身份去重 ——
   * 断线重连会产生新的一行记录,只按昵称去重会在同名用户之间误合并。
   */
  const onlineParticipants = useMemo(() => {
    const byIdentity = new Map<string, typeof participants[number]>();
    for (const item of participants) {
      if (item.connection_status !== "connected") continue;
      // 优先用 livekit_identity(每次连接唯一且与音视频轨对应),
      // 其次 username(同一账号多端登录时合并为一人),最后才用行 id。
      const key = item.username || item.livekit_identity || item.id;
      const existing = byIdentity.get(key);
      // 同一身份有多条时保留主持人角色,避免重连后角色标记丢失
      if (!existing || (existing.role !== "host" && item.role === "host")) byIdentity.set(key, item);
    }
    return [...byIdentity.values()];
  }, [participants]);

  /**
   * 按稳定身份解析发言人姓名。
   *
   * 绝不回退到当前登录用户 —— 那样会把别人的发言标成自己的名字。
   * 解析不出来时宁可显示身份标识或中性文案,也不能张冠李戴。
   */
  const resolveSpeakerName = useMemo(() => {
    const byIdentity = new Map<string, string>();
    for (const item of participants) {
      if (item.livekit_identity) byIdentity.set(item.livekit_identity, item.display_name);
      if (item.username) byIdentity.set(item.username, item.display_name);
    }
    return (caption: CaptionItem): string => {
      const resolved =
        caption.speaker_name
        || (caption.participant_identity ? byIdentity.get(caption.participant_identity) : undefined)
        || (caption.speaker_username ? byIdentity.get(caption.speaker_username) : undefined);
      if (resolved) {
        // 本人加标识,但不覆盖真实姓名
        const isSelf = caption.participant_identity === session.identity
          || caption.speaker_username === user.username;
        return isSelf ? `${resolved}（我）` : resolved;
      }
      return caption.participant_identity || "发言人";
    };
  }, [participants, session.identity, user.username]);

  const crowded = onlineParticipants.length > 25;
  useEffect(() => {
    const refresh = () => {
      request<RealtimeMeeting & { participants: typeof participants }>(`/api/meetings/${session.meeting.id}`).then(item => {
        if (item.status === "ended") leave(true);
        else {
          setMeetingStatus(item.status);
          // 内容未变化时保留原引用,避免每轮轮询都触发整棵会议组件树重渲染
          setParticipants(previous => JSON.stringify(previous) === JSON.stringify(item.participants || []) ? previous : (item.participants || []));
        }
      }).catch(() => undefined);
      request<{ items: typeof chat }>(`/api/meetings/${session.meeting.id}/chat`).then(item => {
        const next = item.items.slice(-400);
        setChat(previous => previous.length === next.length && previous.at(-1)?.id === next.at(-1)?.id ? previous : next);
      }).catch(() => undefined);
    };
    refresh(); const timer = window.setInterval(refresh, crowded ? 8000 : 2500); return () => clearInterval(timer);
    // leave 未列入依赖:它是父组件每次渲染新建的内联箭头函数,列进来会让轮询
    // 定时器每渲染一次就重建一次。核实过它捕获的都是稳定值(会议 id 不变、
    // exitToLobby 只依赖 ref 与 props),旧闭包与新闭包行为一致。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.meeting.id, crowded]);
  const transcriptCursor = useRef(0);
  // 游标只在换会议时归零。不能把重置写进下面的轮询 effect:那个 effect 依赖 crowded,
  // 连接人数跨过 25 人阈值时(有人进出会议)就会重跑,顺带清零游标会让整场会议的
  // 转写被从头全量重拉一遍 —— 长会有上万条分段,人数在阈值附近波动时会反复触发。
  useEffect(() => { transcriptCursor.current = 0; }, [session.meeting.id]);
  useEffect(() => {
    if (!session.asr_enabled) return;
    // 增量拉取:只取上次之后的新句子,避免长会全量转写反复传输
    const refreshTranscript = () => request<{ items: Array<CaptionItem & { is_final: number; created_at: number }> }>(
      `/api/meetings/${session.meeting.id}/transcript?after=${transcriptCursor.current}`,
    ).then(data => {
      if (!data.items.length) return;
      transcriptCursor.current = Math.max(...data.items.map(item => item.created_at), transcriptCursor.current);
      const finalItems = data.items.filter(item => Boolean(item.is_final)).map(item => ({ ...item, type: "transcript.final" }));
      if (!finalItems.length) return;
      setCaptions(previous => {
        const keyed = new Map(previous
          .filter(item => item.type === "transcript.final" || item.type === "asr.error")
          .map(item => [`${item.participant_identity}-${item.sentence_id}-${item.text}`, item] as const));
        for (const item of finalItems) keyed.set(`${item.participant_identity}-${item.sentence_id}-${item.text}`, item);
        return [...keyed.values()].slice(-120);
      });
    }).catch(() => undefined);
    refreshTranscript();
    const timer = window.setInterval(refreshTranscript, crowded ? 5000 : 1500);
    return () => window.clearInterval(timer);
  }, [session.asr_enabled, session.meeting.id, crowded]);
  const onCaption = useMemo(() => (item: CaptionItem) => setCaptions(previous => {
    if (item.type === "asr.error") return [...previous, item].slice(-80);
    if (item.type === "transcript.final") {
      const normalized = item.text.replace(/\s+/g, "").trim();
      const now = Date.now();
      if (normalized && normalized === lastFinalCaption.current.text && now - lastFinalCaption.current.at < 45_000) return previous;
      lastFinalCaption.current = { text: normalized, at: now };
    }
    const next = previous.filter(existing => !(existing.sentence_id === item.sentence_id && existing.type !== "transcript.final"));
    return [...next, item].slice(-80);
  }), []);
  const finish = async () => {
    if (ending) return;
    if (!window.confirm("结束会议后，所有参会者都将离开。确定结束会议吗？")) return;
    setEnding(true);
    try { await request(`/api/meetings/${session.meeting.id}/end`, { method: "POST" }); leave(true); }
    catch { setEnding(false); }
  };
  // 「不等了，现在就开」。后端的 /start 一直都在,但此前前端从未调用过 ——
  // 于是人提前到齐时没有任何办法开始:只能干等到点,或者照常说话,而到点之前
  // 说的话在数据起点之下,不会进入纪要。预约会议要的就是「提前进来调设备,
  // 或者直接开会」这两条路,这里补上后一条。
  const [starting, setStarting] = useState(false);
  const startNow = async () => {
    if (starting) return;
    setStarting(true);
    try {
      const started = await request<RealtimeMeeting>(`/api/meetings/${session.meeting.id}/start`, { method: "POST" });
      setMeetingStatus(started.status);
    } finally { setStarting(false); }
  };
  const sendChat = async () => {
    if (!chatText.trim() || chatSending) return;
    setChatSending(true); setChatError("");
    try {
      const item = await request<{ id: string; sender_name: string; message: string; created_at: number }>(`/api/meetings/${session.meeting.id}/chat`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: chatText }),
      });
      setChat(previous => previous.some(existing => existing.id === item.id) ? previous : [...previous, item]); setChatText("");
    } catch (error) { setChatError((error as Error).message); }
    finally { setChatSending(false); }
  };
  const toggleLock = async () => {
    const action = session.meeting.is_locked ? "unlock" : "lock";
    const updated = await request<RealtimeMeeting>(`/api/meetings/${session.meeting.id}/${action}`, { method: "POST" });
    session.meeting.is_locked = updated.is_locked;
    setPanel("info");
  };
  const revealStageControls = () => {
    setStageControlsVisible(true);
    window.clearTimeout(stageControlTimer.current);
    stageControlTimer.current = window.setTimeout(() => setStageControlsVisible(false), 4000);
  };
  const enterTargetFullscreen = async (target?: Element | null) => {
    if (softFullscreen) return setSoftFullscreen(false);
    if (document.fullscreenElement) return document.exitFullscreen();
    const element = (target || shellRef.current) as (HTMLElement & { webkitRequestFullscreen?: () => Promise<void> | void }) | null;
    if (element?.requestFullscreen) return element.requestFullscreen();
    if (element?.webkitRequestFullscreen) return element.webkitRequestFullscreen();
    // iOS Safari 不支持对普通元素全屏,只有 <video> 能全屏。原先在这里退到
    // video.webkitEnterFullscreen(),会唤起 iOS 的原生播放器 —— 那个播放器按点播
    // 文件的模式工作(带进度条和暂停键),放不了 WebRTC 实时流,画面直接卡住。
    // 改为用 CSS 把舞台铺满视口:video 仍在原处内联播放,只是视觉上占满全屏。
    setSoftFullscreen(true);
  };
  // 全屏放大哪一块:先看有没有聚焦中的主画面(用户点选的那块),
  // 其次是共享屏幕,最后退回第一块磁贴。顺序必须和 styles-release.css
  // 里软全屏那组规则保持一致,否则两端会各放大各的。
  const preferredFullscreenTile = () => {
    const stage = shellRef.current?.querySelector(".conference-stage");
    const focused = stage?.querySelector(".lk-focus-layout > .lk-participant-tile");
    const shared = stage?.querySelector('.lk-participant-tile[data-lk-source="screen_share"]');
    return focused || shared || stage?.querySelector(".lk-participant-tile") || stage;
  };
  const toggleFullscreen = () => enterTargetFullscreen(preferredFullscreenTile());
  // 点磁贴 = 切主画面,而不是直接全屏。LiveKit 每块磁贴里自带一个聚焦按钮,
  // 我们把它藏起来(手机上那个小按钮太难点),改成点磁贴任意位置触发它 ——
  // 复用 LiveKit 自己的聚焦布局,再点一次即可取消聚焦。
  const tapTileFocus = (event: React.MouseEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    if (target.closest("button, input, select, textarea, .lk-control-bar")) return;
    const tile = target.closest(".lk-participant-tile");
    const toggle = tile?.querySelector<HTMLButtonElement>(".lk-focus-toggle-button");
    if (toggle) toggle.click();
  };
  const shareScreen = async () => {
    setShareNotice("");
    if (!navigator.mediaDevices?.getDisplayMedia) {
      setShareNotice("当前手机浏览器不支持发起屏幕共享；可以正常观看他人共享，发起共享请使用桌面版 Chrome 或 Edge。");
      return;
    }
    // 始终请求音频,把「要不要声音」交给 Chrome 弹窗里它自己的开关 ——
    // 那个开关就在选择画面的同一个弹窗里,比我们在页面上另做一个更顺手,
    // 而且它还能按页签类型自动决定是否可用(macOS 共享整屏时系统不给系统声音)。
    // 仅限 Chromium:Safari 的 getDisplayMedia 不支持 audio,带上会让整个调用直接失败。
    const supportsShareAudio = /Chrome|Chromium|Edg/.test(navigator.userAgent) && !/Firefox/.test(navigator.userAgent);
    const wantAudio = supportsShareAudio && !localParticipant.isScreenShareEnabled;
    try {
      await localParticipant.setScreenShareEnabled(!localParticipant.isScreenShareEnabled, {
        // 共享屏幕内的声音(播放视频、演示带音频时需要)。浏览器支持差异很大:
        // Windows 的 Chrome/Edge 共享整屏时可带系统声音;macOS 的 Chrome 只有
        // 共享「标签页」时才能带声音,共享整屏拿不到 —— 这是系统限制,不是代码问题。
        // 所以下面在拿到轨道后要核对一次,没拿到就明确告诉用户原因。
        audio: wantAudio,
        systemAudio: wantAudio ? "include" : "exclude",
        // 会议里绝大多数共享是整屏演示,让 Chrome 的选择弹窗默认停在「整个屏幕」,
        // 省掉每次都要先切一次页签。这只是首选项,用户仍可自行切到标签页或窗口。
        // 注意:页签的排列顺序由浏览器决定,网页无法调整,只能决定默认选中哪个。
        video: { displaySurface: "monitor" },
        // 共享整屏时把本会议标签页排除在候选之外,避免选中自己造成无限镜像画面。
        selfBrowserSurface: "exclude",
        // 保留「切换共享源」控件,共享中途换屏不必先停止再重开。
        surfaceSwitching: "include",
      });
      if (wantAudio && localParticipant.isScreenShareEnabled) {
        const hasAudio = Boolean(localParticipant.getTrackPublication(Track.Source.ScreenShareAudio));
        if (!hasAudio) {
          setShareNotice("已开始共享画面，但没有带上声音。若需要共享声音：在选择弹窗里切到「Chrome 标签页」，选中标签页后打开左下角的「分享标签页音频」。macOS 共享整个屏幕时系统不提供声音采集，这是系统限制。");
        }
      }
    } catch (error) {
      const message = error instanceof Error && error.name === "NotAllowedError" ? "未获得屏幕共享权限，请在系统弹窗中选择允许。" : "屏幕共享启动失败，请检查浏览器权限后重试。";
      setShareNotice(message);
    }
  };
  const toggleMicrophone = async () => {
    setMediaError("");
    try { await localParticipant.setMicrophoneEnabled(!localParticipant.isMicrophoneEnabled); }
    catch { setMediaError("麦克风启动失败，请在浏览器地址栏中允许筑听使用麦克风。"); }
  };


  const toggleCamera = async () => {
    setMediaError("");
    try { await localParticipant.setCameraEnabled(!localParticipant.isCameraEnabled); }
    catch { setMediaError("摄像头启动失败，请在浏览器地址栏中允许筑听使用摄像头。"); }
  };
  const latest = captions.at(-1);
  return <div ref={shellRef} className={`conference-shell ${softFullscreen ? "soft-fullscreen" : ""}`} data-lk-theme="default">
    {meetingStatus === "scheduled" && <div className="meeting-preview-notice">
      <span>设备测试中 · 预约时间前退出不会结束会议，到点后自动进入正式会议</span>
      {(session.role === "host" || user.username === "admin") && <button type="button" disabled={starting} onClick={startNow}>{starting ? "正在开始…" : "现在就开始"}</button>}
    </div>}
    <header className="conference-topbar"><div><span className="conference-mark" /> <b className="conference-title" title={session.meeting.title}>{session.meeting.title}</b><span className="conference-code" title="会议号">{session.meeting.meeting_code}</span><time>{formatTime(elapsed)}</time><i className="network-bars" aria-label="网络状态良好"><span /><span /><span /></i></div><div><button aria-pressed={panel === "members"} onClick={() => setPanel(panel === "members" ? null : "members")}><Users />成员({onlineParticipants.length})</button><button aria-pressed={panel === "info"} onClick={() => setPanel(panel === "info" ? null : "info")}><ShieldCheck />会议信息</button></div></header>
    <div className={`conference-stage ${panel ? "with-panel" : ""}`} onPointerDown={revealStageControls} onClick={tapTileFocus}><VideoConference key={conferenceRevision} /><RoomAudioRenderer /><button className={`stage-fullscreen ${stageControlsVisible ? "visible" : ""}`} onPointerDown={event => event.stopPropagation()} onClick={event => { event.stopPropagation(); void toggleFullscreen(); }} title={fullscreen || softFullscreen ? "退出全屏" : "优先全屏共享画面"} aria-label={fullscreen || softFullscreen ? "退出全屏" : "优先全屏共享画面"}>{fullscreen || softFullscreen ? <Minimize2 /> : <Maximize2 />}</button></div>
    {latest?.text && <div className={`live-caption ${latest.type === "asr.error" ? "error" : ""}`}><span>{latest.type === "transcript.final" ? resolveSpeakerName(latest) : "识别中"}</span>{latest.text}</div>}
    {panel && <aside className="conference-side" aria-label={panel === "captions" ? "实时转录" : panel === "members" ? "参会成员" : panel === "chat" ? "会议聊天" : "会议信息"}><header><div>{panel === "chat" ? <MessageSquare /> : panel === "members" ? <Users /> : panel === "captions" ? <Captions /> : <ShieldCheck />}<b>{panel === "captions" ? "实时转录" : panel === "members" ? "参会成员" : panel === "chat" ? "会议聊天" : "会议信息"}</b></div><button className="icon" aria-label="关闭侧栏" onClick={() => setPanel(null)}><X /></button></header>
      {panel === "captions" ? <div className="caption-timeline" ref={captionScrollRef}>{captions.filter(item => item.type === "transcript.final" || item.type === "asr.error").map((item, index) => <div key={`${item.participant_identity || "local"}-${item.sentence_id}-${index}`}><time>{formatTime(Math.floor((item.start_time_ms || 0) / 1000))}</time><p>{<b>{resolveSpeakerName(item)}：</b>}{item.text}</p></div>)}{!captions.length && <div className="caption-empty"><Captions /><b>等待发言</b><span>开启麦克风后，实时字幕会自动显示在这里</span></div>}</div>
        : panel === "members" ? <div className="participant-panel">{onlineParticipants.map(item => <div key={item.username || item.livekit_identity || item.id}><span>{item.display_name.slice(0, 1)}</span><p><b>{item.display_name}{item.role === "host" ? "（主持人）" : ""}{(item.username && item.username === user.username) ? "（我）" : ""}</b></p></div>)}</div>
        : panel === "chat" ? <div className="meeting-chat"><div className="meeting-chat-messages" aria-live="polite" ref={chatScrollRef}>{chat.map(item => <article key={item.id}><header><b>{item.sender_name}</b><time>{new Date(item.created_at * 1000).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</time></header><p>{item.message}</p></article>)}{!chat.length && <div className="meeting-chat-empty"><MessageSquare /><b>还没有消息</b><span>发送第一条会议消息</span></div>}</div>{chatError && <p className="meeting-chat-error" role="alert">{chatError}</p>}<footer><input aria-label="会议消息" value={chatText} onChange={event => setChatText(event.target.value)} onKeyDown={event => event.key === "Enter" && !event.nativeEvent.isComposing && void sendChat()} placeholder="输入会议消息..." /><button disabled={chatSending || !chatText.trim()} onClick={sendChat}>{chatSending ? "发送中" : "发送"}</button></footer></div>
        : <div className="meeting-info-panel"><label>会议主题<strong>{session.meeting.title}</strong></label><label>会议号<strong>{session.meeting.meeting_code}</strong></label><button onClick={() => void copyInvitation(session.meeting, user)}><Copy />复制会议邀请</button>{session.role === "host" && <button onClick={toggleLock}><Lock />{session.meeting.is_locked ? "解除会议锁定" : "锁定会议"}</button>}<p><Lock />转录和纪要仅对筑听授权用户开放。</p></div>}
    </aside>}
    {shareNotice && <div className="screen-share-notice" role="status">{shareNotice}<button aria-label="关闭共享提示" onClick={() => setShareNotice("")}><X /></button></div>}
    {mediaError && <div className="meeting-media-error" role="alert">{mediaError}<button aria-label="关闭媒体提示" onClick={() => setMediaError("")}><X /></button></div>}
    <nav className="conference-media-controls" aria-label="会议媒体控制">
      <button className="media-toggle" data-enabled={localParticipant.isMicrophoneEnabled} onClick={toggleMicrophone}>{localParticipant.isMicrophoneEnabled ? <Mic /> : <MicOff />}{localParticipant.isMicrophoneEnabled ? "静音" : "解除静音"}</button>
      <button className="media-toggle" data-enabled={localParticipant.isCameraEnabled} onClick={toggleCamera}>{localParticipant.isCameraEnabled ? <Video /> : <VideoOff />}{localParticipant.isCameraEnabled ? "关闭视频" : "开启视频"}</button>
      <button data-enabled={localParticipant.isScreenShareEnabled} onClick={shareScreen}><MonitorUp />{localParticipant.isScreenShareEnabled ? "停止共享" : "共享屏幕"}</button>
      <button className="leave-meeting" onClick={() => leave()}><LogOut />离开会议</button>
      {/* 手机端入口:桌面端这些按钮本就排在右下角,不需要这层折叠 */}
      <button className="more-control" aria-expanded={moreOpen} aria-label="更多" onClick={() => setMoreOpen(value => !value)}><MoreHorizontal />更多</button>
    </nav>
    {moreOpen && <div className="conference-more-scrim" onClick={() => setMoreOpen(false)} />}
    <div className={`conference-utility ${moreOpen ? "open" : ""}`}><button className="mobile-share-control" aria-pressed={localParticipant.isScreenShareEnabled} onClick={shareScreen}><MonitorUp />{localParticipant.isScreenShareEnabled ? "停止共享" : "共享屏幕"}</button><button className={panel === "chat" ? "active" : ""} onClick={() => { setPanel(panel === "chat" ? null : "chat"); setMoreOpen(false); }}><MessageSquare />聊天</button><button className={panel === "captions" ? "active" : ""} onClick={() => { setPanel(panel === "captions" ? null : "captions"); setMoreOpen(false); }}><Captions />字幕与转录</button>{meetingStatus === "active" && (session.role === "host" || user.username === "admin") && <button className="end-meeting" disabled={ending} onClick={finish}>{ending ? "正在结束..." : "结束会议"}</button>}</div>
    <RealtimeAsr meeting={session.meeting} identity={session.identity} enabled={session.asr_enabled && meetingStatus === "active"} onCaption={onCaption} />
  </div>;
}

export function MeetingRoom({ session, user, onLeave }: { session: JoinResponse; user: User; onLeave: (ended?: boolean) => void }) {
  const leaving = useRef(false);
  const reconnectTimer = useRef<number | undefined>(undefined);
  const [live, setLive] = useState(session);
  const [connectionAttempt, setConnectionAttempt] = useState(0);
  // 接入阶段用本地化提示;LiveKit 自带的英文 toast 已在样式中隐藏(深色字压深色底不可读)
  const [connectionNotice, setConnectionNotice] = useState("正在接入会议，请稍候…");
  useEffect(() => () => window.clearTimeout(reconnectTimer.current), []);
  const exitToLobby = (ended?: boolean) => {
    clearMeetingInviteFromUrl();
    onLeave(ended);
  };
  const leave = async () => {
    if (leaving.current) return;
    leaving.current = true;
    try { await request(`/api/meetings/${live.meeting.id}/leave`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); }
    catch { /* A failed status update must never trap the user inside a room. */ }
    finally { exitToLobby(); }
  };
  const reconnect = (reason?: DisconnectReason) => {
    if (leaving.current || reason === DisconnectReason.CLIENT_INITIATED) return;
    setConnectionNotice("会议连接中断，正在自动重新连接...");
    window.clearTimeout(reconnectTimer.current);
    reconnectTimer.current = window.setTimeout(async () => {
      // LiveKit token 有效期有限,长会中断线必须重新 join 换新 token,直接复用旧 token 会被拒绝
      try {
        const fresh = await request<JoinResponse>(`/api/meetings/${live.meeting.id}/join`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ display_name: user.display_name }),
        });
        setLive(fresh);
        setConnectionAttempt(value => value + 1);
        setConnectionNotice("");
      } catch (error) {
        setConnectionNotice((error as Error).message || "会议已结束或暂时无法重连");
        window.setTimeout(() => exitToLobby(true), 2500);
      }
    }, 1200);
  };
  return <>
    <LiveKitRoom key={connectionAttempt} token={live.token} serverUrl={live.livekit_url} connect audio={false} video={false} options={ROOM_OPTIONS} onConnected={() => setConnectionNotice("")} onDisconnected={reconnect} onError={error => { console.error("LiveKit", error); setConnectionNotice("媒体连接正在恢复，请稍候..."); }}>
      <MeetingExperience session={live} user={user} leave={ended => ended ? exitToLobby(true) : void leave()} />
    </LiveKitRoom>
    {connectionNotice && <div className="meeting-connection-notice" role="status">{connectionNotice}</div>}
  </>;
}
