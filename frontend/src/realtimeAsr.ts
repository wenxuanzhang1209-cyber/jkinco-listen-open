/** 实时转写的前端侧:把麦克风音频重采样成 16k PCM，推给我们自己的 ASR 代理。
 *
 * 会议字幕和录音面板共用这里。抽出来的直接原因是录音面板原先用的是浏览器自带的
 * Web Speech API —— 那跑在浏览器厂商的通用中文模型上，拿不到我们的热词表，
 * 「监理」「旁站」「检验批」这类工程词几乎必错。改走这条链路后，两处用的是
 * 同一个模型、同一份热词。
 */

/** 浏览器给的是设备采样率的 Float32，上游要 16k 单声道 PCM16。 */
export function resampleToPcm16(input: Float32Array, inputRate: number, outputRate = 16_000): Int16Array {
  if (!input.length || inputRate <= 0 || outputRate <= 0) return new Int16Array();
  const ratio = inputRate / outputRate;
  const outputLength = Math.max(1, Math.floor(input.length / ratio));
  const output = new Int16Array(outputLength);
  for (let outputIndex = 0; outputIndex < outputLength; outputIndex += 1) {
    const start = Math.floor(outputIndex * ratio);
    const end = Math.min(input.length, Math.max(start + 1, Math.floor((outputIndex + 1) * ratio)));
    let sum = 0;
    for (let inputIndex = start; inputIndex < end; inputIndex += 1) sum += input[inputIndex];
    const sample = Math.max(-1, Math.min(1, sum / (end - start)));
    output[outputIndex] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  return output;
}

export type AsrMessage = {
  type: "asr.ready" | "asr.error" | "transcript.interim" | "transcript.final";
  text?: string;
  sentence_id?: number;
  message?: string;
};

export type RealtimeAsrHandle = { stop: () => void };

/** 接上实时转写，返回一个只需调用 stop() 的句柄。
 *
 * 断线要自己重连:录音可能持续一小时，中途上游断开是常态，而用户不会知道
 * 字幕为什么停了 —— 停在那里比转错更难察觉。
 */
export function startRealtimeAsr(options: {
  stream: MediaStream;
  onMessage: (item: AsrMessage) => void;
  onStatus: (message: string) => void;
  /** 重连前再问一次调用方。录音可能被浏览器单方面终止(麦克风被系统回收、
   *  设备拔出)，那条路上没人会调用 stop()，少了这道闸重连就会空转到上限。 */
  shouldContinue?: () => boolean;
}): RealtimeAsrHandle {
  const { stream, onMessage, onStatus, shouldContinue } = options;
  let active = true;
  let socket: WebSocket | undefined;
  let context: AudioContext | undefined;
  let source: MediaStreamAudioSourceNode | undefined;
  let processor: ScriptProcessorNode | undefined;
  let silentOutput: GainNode | undefined;
  let restartTimer: number | undefined;
  let restarts = 0;
  // 重试计数在什么时候才该清零。
  //
  // 原先是一收到 asr.ready 就清 —— 那意味着「连上→很快断→重连」这种循环里
  // 计数永远回不到上限,30 次的闸门形同虚设。服务端加了空闲超时之后这条路真的
  // 可达:音频图被浏览器挂起(比如手机切到后台)时,每一轮都会连上、再因为收不到
  // 音频被服务端关掉,无限循环。
  //
  // 「连了多久」不是可靠信号:空闲超时本身就有 120 秒,按时长判定照样会被判成
  // 稳定。真正的信号是**这条连接有没有产出过转写** —— 没有音频就不会有转写,
  // 有音频就说明链路真的在干活。

  const teardownAudio = () => {
    processor?.disconnect();
    source?.disconnect();
    silentOutput?.disconnect();
    processor = undefined;
    source = undefined;
    silentOutput = undefined;
    // AudioContext 不关掉的话，每次重连都会新建一个 —— 浏览器对同时存在的
    // AudioContext 数量有上限，长录音重连几十次就会连不上。
    context?.close().catch(() => { /* 已关闭 */ });
    context = undefined;
  };

  const alive = () => active && (!shouldContinue || shouldContinue());

  const scheduleRestart = () => {
    // 一次失败会同时触发 onerror(走 connect 的 catch)和 onclose,两条都会排重连。
    // 不先清掉上一个定时器的话，restartTimer 只是被覆盖，旧的照样到点触发 ——
    // 于是并行建起两条 WebSocket,各自一条计费上行。
    window.clearTimeout(restartTimer);
    if (!alive()) return;
    if (restarts >= 30) {
      onStatus("实时转写已断开，录音仍在继续保存");
      teardownAudio();
      return;
    }
    restarts += 1;
    restartTimer = window.setTimeout(() => {
      if (alive()) connect().catch(() => scheduleRestart());
    }, Math.min(3000 * restarts, 15_000));
  };

  const connect = async () => {
    teardownAudio();
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const pending = new WebSocket(`${protocol}//${location.host}/api/realtime/asr`);
    socket = pending;
    pending.binaryType = "arraybuffer";
    pending.onmessage = event => {
      const item = JSON.parse(event.data) as AsrMessage;
      // 收到转写才算这条连接真的可用 —— 见 restarts 的注释。
      if (item.type === "transcript.interim" || item.type === "transcript.final") restarts = 0;
      onMessage(item);
    };
    pending.onclose = () => { if (active && socket === pending) scheduleRestart(); };
    await new Promise<void>((resolve, reject) => {
      pending.onopen = () => resolve();
      pending.onerror = () => reject(new Error("实时转写连接失败"));
    });
    if (!active) { pending.close(); return; }

    const audio = new AudioContext();
    context = audio;
    await audio.resume();
    // resume() 是个 await 点:停止录音可能正好落在这里，teardownAudio() 会把
    // context 置空,之后再用它就是 TypeError。虽然外层 catch 兜得住,但那是靠
    // 异常收场而不是逻辑正确 —— 这里用局部变量 + 显式检查,让它本来就不会发生。
    if (!alive() || context !== audio) {
      audio.close().catch(() => { /* 已关闭 */ });
      return;
    }
    source = audio.createMediaStreamSource(stream);
    processor = audio.createScriptProcessor(4096, 1, 1);
    silentOutput = audio.createGain();
    // 必须接到 destination 上 ScriptProcessor 才会被驱动，但不能真的出声 ——
    // 否则用户会从扬声器听到自己的回声。
    silentOutput.gain.value = 0;
    processor.onaudioprocess = event => {
      if (!active || pending.readyState !== WebSocket.OPEN) return;
      const pcm = resampleToPcm16(event.inputBuffer.getChannelData(0), event.inputBuffer.sampleRate);
      if (!pcm.length) return;
      pending.send(pcm.buffer as ArrayBuffer);
    };
    source.connect(processor);
    processor.connect(silentOutput);
    silentOutput.connect(audio.destination);
  };

  connect().catch(() => scheduleRestart());

  return {
    stop: () => {
      active = false;
      window.clearTimeout(restartTimer);
      if (socket) {
        // 先摘掉 onclose，否则主动关闭会被当成掉线又拉起一次重连。
        socket.onclose = null;
        // finish 让上游把最后一句话吐完，也让计费上行确定性地收尾。
        if (socket.readyState === WebSocket.OPEN) {
          try { socket.send(JSON.stringify({ type: "finish" })); } catch { /* 已断开 */ }
        }
        socket.close();
        socket = undefined;
      }
      teardownAudio();
    },
  };
}
