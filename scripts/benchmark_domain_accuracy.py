#!/usr/bin/env python3
"""领域词识别准确率基准 —— 任何人都能自己跑一遍。

README 里那个 30/36 → 35/36 的数字就是这个脚本产出的。把它放进仓库，是因为
一个不能被别人复现的基准和一句形容词没有区别。

    python3 scripts/benchmark_domain_accuracy.py

它做四件事：

  1. 用 macOS 的 `say` 合成十段中文会议音频（Linux 上用 espeak-ng，见下）；
  2. 用 ffmpeg 加粉噪、提速、限幅，模拟真实会议室的录音条件 —— 干净的 TTS
     音频测不出东西来，模型在它上面几乎不犯错；
  3. 过一遍本地 paraformer-zh；
  4. 统计 36 个领域词的命中，并对比「加领域纠错前后」。

音频是合成的，这一点必须说在前面：它比真实会议室干净得多，所以这个分数是**上界**，
不是你在现场能拿到的分数。它的价值在于**可比较**——同一批音频、同一套目标词，
换模型、改词库、调参数之后再跑一遍，差值是可信的。

依赖：ffmpeg 与 `pip install funasr torch torchaudio numpy`。`say` 是 macOS 自带的。
（torchaudio 是 funasr 的隐式依赖 —— 少了它会在加载模型时才失败，所以这里一并写明。）
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: 十段素材。每段一句工程/人事/商务会议里真会说的话，后面跟着这段要考的领域词。
#: 选词标准和词库一致：**容易被听错，且错了代价大**。
CLIPS: list[tuple[str, str, list[str]]] = [
    ("supervision",
     "今天监理单位对这个检验批做了平行检验，旁站记录已经归档。",
     ["监理单位", "检验批", "平行检验", "旁站记录"]),
    ("high-risk",
     "这个属于危大工程，专项施工方案必须经过专家论证。",
     ["危大工程", "专项施工方案", "专家论证"]),
    ("sampling",
     "钢筋原材复试要见证取样送检，见证员和取样员都要签字。",
     ["钢筋原材复试", "见证取样送检", "见证员", "取样员"]),
    ("slump",
     "混凝土进场时坍落度不合格，同条件试块也要重做。",
     ["坍落度", "同条件试块"]),
    ("interview",
     "候选人的背景调查已经做完，薪资期望和到岗时间还要再谈。",
     ["候选人", "背景调查", "薪资期望", "到岗时间"]),
    ("commercial",
     "客户的商务条款还没谈拢，回款节点和验收标准要写进合同。",
     ["商务条款", "回款", "验收标准"]),
    ("settlement",
     "这个项目中标以后，结算和产值要分开算，回款按付款节点走。",
     ["中标", "结算", "产值", "付款节点"]),
    ("organisation",
     "上海建科咨询这边由事业部牵头，雄安质量的报告下周交付。",
     ["上海建科咨询", "事业部", "雄安质量"]),
    ("ai-platform",
     "这个智能体要接多模态的大模型，向量库和知识库都要打通。",
     ["智能体", "多模态", "大模型", "向量库", "知识库"]),
    ("permissions",
     "数据权限按三级目录配，角色权限和字段映射要对齐。",
     ["数据权限", "三级目录", "角色权限", "字段映射"]),
]

#: 噪声与提速参数。刻意调到「还能听清但模型开始犯错」的程度：
#: 干净 TTS 上模型几乎全对，那样的基准分不出任何东西。
NOISE_FILTER = (
    "[0:a]atempo=1.2,volume=2.0,alimiter=limit=0.85[s];"
    "anoisesrc=d=60:c=pink:a=0.04[n];"
    "[s][n]amix=inputs=2:duration=first[o]"
)


def _require(binary: str, hint: str) -> None:
    if shutil.which(binary) is None:
        sys.exit(f"缺少 {binary}。{hint}")


def synthesise(text: str, aiff: Path) -> None:
    """合成一段语音。macOS 用 say，Linux 退回 espeak-ng。"""
    if shutil.which("say"):
        subprocess.run(["say", "-v", "Tingting", "-r", "185", "-o", str(aiff), text], check=True)
    elif shutil.which("espeak-ng"):
        subprocess.run(["espeak-ng", "-v", "cmn", "-s", "170", "-w", str(aiff), text], check=True)
    else:
        sys.exit("需要 macOS 的 `say` 或 Linux 的 espeak-ng 来合成音频。")


def degrade(aiff: Path, pcm: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff),
         "-filter_complex", NOISE_FILTER, "-map", "[o]",
         "-ar", "16000", "-ac", "1", "-f", "s16le", str(pcm)],
        check=True,
    )


#: 比对前要剥掉的标点。
#:
#: 模型会插入断句标点，而它插的位置不一定尊重词的边界 —— 实测「见证取样送检」
#: 被断成了「见证取样，送检」：**每个字都对**，只是中间多了个逗号。
#: 按原样比对会把它算成识别失败，那是在低估模型，也会让人误以为词库该为此负责。
#: 领域词识别的正确与否与标点无关，所以两边都剥掉再比。
_PUNCTUATION = "，。、？！；：""''（）()【】,.?!;: \n\t"


def strip_punctuation(text: str) -> str:
    return "".join(ch for ch in text if ch not in _PUNCTUATION)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep-audio", metavar="DIR", help="保留生成的音频，方便自己听一遍")
    parser.add_argument("--json", metavar="FILE", help="把结果写成 JSON，便于对比两次运行")
    args = parser.parse_args()

    _require("ffmpeg", "macOS: brew install ffmpeg    Debian/Ubuntu: apt install ffmpeg")
    try:
        import numpy as np
        from funasr import AutoModel
    except ImportError as error:
        sys.exit(f"缺少依赖（{error.name}）。pip install funasr torch torchaudio numpy")

    from jkinco_lexicon import correct_domain_terms

    workdir = Path(args.keep_audio) if args.keep_audio else Path(tempfile.mkdtemp(prefix="jkinco-bench-"))
    workdir.mkdir(parents=True, exist_ok=True)

    print(f"合成并降质 {len(CLIPS)} 段音频 -> {workdir}", flush=True)
    for name, text, _ in CLIPS:
        aiff, pcm = workdir / f"{name}.aiff", workdir / f"{name}.pcm"
        if not pcm.exists():
            synthesise(text, aiff)
            degrade(aiff, pcm)

    print("加载 paraformer-zh（首次运行会下载模型，约 1–2GB）...", flush=True)
    model = AutoModel(model="paraformer-zh", vad_model="fsmn-vad",
                      punc_model="ct-punc", disable_update=True)

    raw_hits = fixed_hits = total = 0
    rows = []
    print(f"\n{'clip':<14}{'raw':>7}{'corrected':>11}   transcript")
    print("-" * 78)
    for name, _, targets in CLIPS:
        audio = np.frombuffer((workdir / f"{name}.pcm").read_bytes(), dtype=np.int16)
        result = model.generate(input=audio.astype(np.float32) / 32768.0, fs=16000)
        text = result[0]["text"] if result else ""
        corrected = correct_domain_terms(text)

        bare_raw, bare_fixed = strip_punctuation(text), strip_punctuation(corrected)
        raw = sum(1 for t in targets if strip_punctuation(t) in bare_raw)
        fixed = sum(1 for t in targets if strip_punctuation(t) in bare_fixed)
        raw_hits += raw
        fixed_hits += fixed
        total += len(targets)
        rows.append({"clip": name, "raw": raw, "corrected": fixed,
                     "targets": len(targets), "transcript": corrected})
        flag = "  <-- fixed" if fixed > raw else ""
        print(f"{name:<14}{raw:>4}/{len(targets)}{fixed:>8}/{len(targets)}   {corrected[:40]}{flag}")

    print("-" * 78)
    print(f"{'TOTAL':<14}{raw_hits:>4}/{total}{fixed_hits:>8}/{total}"
          f"   ({100 * raw_hits / total:.1f}% -> {100 * fixed_hits / total:.1f}%)")

    if fixed_hits == raw_hits and raw_hits < total:
        print("\n注意：领域纠错一个都没修回来。这正是当初发现纠错表问题的信号 ——")
        print("      表里的错法是想象出来的，和模型实际犯的错对不上。")
        print("      把上面的 transcript 拿去和目标词逐个比对，就能看出真实错法长什么样。")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"raw": raw_hits, "corrected": fixed_hits, "total": total, "clips": rows},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已写入 {args.json}")

    if not args.keep_audio:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
