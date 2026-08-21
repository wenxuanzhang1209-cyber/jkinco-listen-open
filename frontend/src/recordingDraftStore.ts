export type RecordingDraftStatus = "recording" | "stopped";

export type RecordingDraftMeta = {
  id: string;
  ownerUsername: string;
  filename: string;
  mimeType: string;
  startedAt: number;
  elapsedSeconds: number;
  transcript: string;
  status: RecordingDraftStatus;
  chunkCount: number;
  updatedAt: number;
};

type RecordingDraftChunk = {
  key: string;
  sessionId: string;
  index: number;
  blob: Blob;
};

const DB_NAME = "jkinco-listen-recording-drafts";
const DB_VERSION = 1;
const META_STORE = "sessions";
const CHUNK_STORE = "chunks";

let dbPromise: Promise<IDBDatabase> | undefined;

function openDatabase(): Promise<IDBDatabase> {
  if (typeof indexedDB === "undefined") {
    return Promise.reject(new Error("当前浏览器不支持本地录音恢复"));
  }
  if (dbPromise) return dbPromise;

  dbPromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      const sessions = database.objectStoreNames.contains(META_STORE)
        ? request.transaction!.objectStore(META_STORE)
        : database.createObjectStore(META_STORE, { keyPath: "id" });
      if (!sessions.indexNames.contains("ownerUsername")) {
        sessions.createIndex("ownerUsername", "ownerUsername", { unique: false });
      }

      const chunks = database.objectStoreNames.contains(CHUNK_STORE)
        ? request.transaction!.objectStore(CHUNK_STORE)
        : database.createObjectStore(CHUNK_STORE, { keyPath: "key" });
      if (!chunks.indexNames.contains("sessionId")) {
        chunks.createIndex("sessionId", "sessionId", { unique: false });
      }
    };
    request.onsuccess = () => {
      const database = request.result;
      database.onversionchange = () => database.close();
      resolve(database);
    };
    request.onerror = () => {
      dbPromise = undefined;
      reject(request.error || new Error("无法打开本地录音存储"));
    };
    request.onblocked = () => {
      dbPromise = undefined;
      reject(new Error("本地录音存储正在被其他页面占用"));
    };
  });
  return dbPromise;
}

function transaction<T>(
  stores: string[],
  mode: IDBTransactionMode,
  callback: (tx: IDBTransaction, resolve: (value: T) => void, reject: (reason?: unknown) => void) => void,
): Promise<T> {
  return openDatabase().then(database => new Promise<T>((resolve, reject) => {
    const tx = database.transaction(stores, mode);
    let settled = false;
    const finish = (value: T) => {
      if (!settled) { settled = true; resolve(value); }
    };
    const fail = (reason?: unknown) => {
      if (!settled) { settled = true; reject(reason || tx.error || new Error("本地录音存储失败")); }
    };
    tx.oncomplete = () => { if (!settled) finish(undefined as T); };
    tx.onerror = () => fail(tx.error || new Error("本地录音存储失败"));
    tx.onabort = () => fail(tx.error || new Error("本地录音存储已中止"));
    try { callback(tx, finish, fail); } catch (error) { fail(error); }
  }));
}

export async function createRecordingDraft(meta: RecordingDraftMeta): Promise<void> {
  await transaction<void>([META_STORE], "readwrite", tx => {
    tx.objectStore(META_STORE).put(meta);
  });
}

export async function appendRecordingChunk(sessionId: string, index: number, blob: Blob): Promise<void> {
  const chunk: RecordingDraftChunk = { key: `${sessionId}:${index}`, sessionId, index, blob };
  await transaction<void>([CHUNK_STORE], "readwrite", tx => {
    tx.objectStore(CHUNK_STORE).put(chunk);
  });
}

export async function updateRecordingDraft(id: string, patch: Partial<RecordingDraftMeta>): Promise<void> {
  await transaction<void>([META_STORE], "readwrite", tx => {
    const request = tx.objectStore(META_STORE).get(id);
    request.onsuccess = () => {
      const current = request.result as RecordingDraftMeta | undefined;
      if (current) tx.objectStore(META_STORE).put({ ...current, ...patch, updatedAt: Date.now() });
    };
  });
}

export async function getLatestRecordingDraft(ownerUsername: string): Promise<RecordingDraftMeta | undefined> {
  return transaction<RecordingDraftMeta | undefined>([META_STORE], "readonly", (tx, resolve) => {
    const request = tx.objectStore(META_STORE).index("ownerUsername").getAll(IDBKeyRange.only(ownerUsername));
    request.onsuccess = () => {
      const items = (request.result as RecordingDraftMeta[]).filter(item => item.chunkCount > 0);
      items.sort((left, right) => right.updatedAt - left.updatedAt);
      resolve(items[0]);
    };
  });
}

export async function readRecordingDraft(id: string): Promise<{ meta: RecordingDraftMeta; blob: Blob }> {
  return transaction<{ meta: RecordingDraftMeta; blob: Blob }>([META_STORE, CHUNK_STORE], "readonly", (tx, resolve, reject) => {
    const metaRequest = tx.objectStore(META_STORE).get(id);
    const chunkRequest = tx.objectStore(CHUNK_STORE).index("sessionId").getAll(IDBKeyRange.only(id));
    let meta: RecordingDraftMeta | undefined;
    let chunks: RecordingDraftChunk[] | undefined;
    const finish = () => {
      if (!meta || !chunks) return;
      chunks.sort((left, right) => left.index - right.index);
      resolve({ meta, blob: new Blob(chunks.map(chunk => chunk.blob), { type: meta.mimeType || "audio/webm" }) });
    };
    metaRequest.onsuccess = () => { meta = metaRequest.result as RecordingDraftMeta | undefined; if (!meta) reject(new Error("录音草稿不存在")); finish(); };
    chunkRequest.onsuccess = () => { chunks = chunkRequest.result as RecordingDraftChunk[]; finish(); };
  });
}

export async function deleteRecordingDraft(id: string): Promise<void> {
  await transaction<void>([META_STORE, CHUNK_STORE], "readwrite", (tx, resolve, reject) => {
    const chunks = tx.objectStore(CHUNK_STORE);
    const request = chunks.index("sessionId").getAllKeys(IDBKeyRange.only(id));
    request.onsuccess = () => {
      for (const key of request.result) chunks.delete(key);
      tx.objectStore(META_STORE).delete(id);
      resolve(undefined);
    };
    request.onerror = () => reject(request.error || new Error("删除录音草稿失败"));
  });
}

/**
 * 清理该用户名下的陈旧草稿,只保留最新的一条。
 *
 * 草稿只在「提交处理成功」时才删除,而失败、超时、中途关页面、录完不提交又重录
 * 这几条路径都会把上一条留在库里。录音按每分钟约 1MB 计,长期使用会把浏览器给
 * 本站的存储配额占满 —— 配额一满,新录音的分片写入就会开始失败,而那正是这套
 * 机制要保护的东西。
 *
 * 保留最新一条是刻意的:界面上的「恢复录音」本来也只认最新那条,更早的既看不见
 * 也用不上,留着只占地方。
 */
export async function pruneRecordingDrafts(ownerUsername: string, keepId?: string): Promise<void> {
  const stale = await transaction<string[]>([META_STORE], "readonly", (tx, resolve) => {
    const request = tx.objectStore(META_STORE).index("ownerUsername").getAll(IDBKeyRange.only(ownerUsername));
    request.onsuccess = () => {
      const items = (request.result as RecordingDraftMeta[]).sort((left, right) => right.updatedAt - left.updatedAt);
      const keep = new Set<string>();
      if (keepId) keep.add(keepId);
      const newest = items.find(item => item.id !== keepId);
      if (newest) keep.add(newest.id);
      resolve(items.filter(item => !keep.has(item.id)).map(item => item.id));
    };
  });
  // 逐条删除而不是开一个大事务:一条失败不该连累其余的清理
  for (const id of stale) {
    await deleteRecordingDraft(id).catch(() => undefined);
  }
}
