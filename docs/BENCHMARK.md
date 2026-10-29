# Domain accuracy benchmark

*[中文版见下方](#领域词识别基准)*

**31/36 → 36/36** — what the local model gets right on domain vocabulary, before and after the
correction layer.

Run it yourself:

```bash
pip install funasr torch torchaudio numpy
python3 scripts/benchmark_domain_accuracy.py
```

One command, no arguments. It synthesises the audio, degrades it, runs the local model, and
prints the table below. Raw results: [`benchmark-results.json`](benchmark-results.json).

---

## What it measures

Ten Chinese meeting sentences, each carrying domain terms that a general-purpose model tends
to mishear — construction, HR, commercial, and platform vocabulary. 36 target
terms in total.

| Clip | Domain | Raw | Corrected |
|---|---|---|---|
| `supervision` | Site review, inspection lots, on-site witness records | 3/4 | **4/4** |
| `high-risk` | High-risk works, expert review | 2/3 | **3/3** |
| `sampling` | Material retesting, witnessed sampling | 3/4 | **4/4** |
| `slump` | Concrete slump, cured test blocks | 0/2 | **2/2** |
| `interview` | Background checks, salary expectations | 4/4 | 4/4 |
| `commercial` | Terms, payment collection, acceptance criteria | 3/3 | 3/3 |
| `settlement` | Bid award, settlement, output value | 4/4 | 4/4 |
| `organisation` | Organisation and division names | 3/3 | 3/3 |
| `ai-platform` | Agents, multimodal, vector stores | 5/5 | 5/5 |
| `permissions` | Data permissions, role mapping | 4/4 | 4/4 |
| **Total** | | **31/36 (86.1%)** | **36/36 (100%)** |

Every failure was a construction term. That is the pattern the correction layer exists for.

## How the correction table was built

Not by imagining plausible typos. By **running this benchmark and writing down what the model
actually produced**:

| What a human would guess | What the model actually said |
|---|---|
| `旁占` | `航站记录` — 「航站」 is a common word, so the model prefers it |
| `危大公程` | `微大工程` — similar initials |
| `塌落度` | `石滩落度` — it mis-heard the preceding character too |

When this benchmark first ran, **not one of the original thirteen correction rules fired.**
They had all been written from imagination. Five rules derived from measured output fixed
every remaining failure.

## Two things this benchmark is not

**It is not a real-world accuracy figure.** The audio is synthesised with `say` and degraded
with pink noise and a tempo shift. That is harder than clean TTS — without the degradation the
model scores near-perfect and the benchmark measures nothing — but it is still cleaner than a
room with four people, a projector fan, and someone on speakerphone. **Treat the number as an
upper bound.**

**It is not a comparison against other products.** It measures this pipeline against itself.
Its value is the delta: change the model, edit the lexicon, adjust a parameter, run it again —
that difference is trustworthy in a way an absolute score is not.

## A measurement bug worth recording

The first version compared target words against the transcript directly, and scored 35/36. The
missing one was `见证取样送检`, which the model had transcribed as `见证取样，送检` — **every
character correct**, with a sentence-boundary comma inserted in the middle of the term.

That is a punctuation placement artefact, not a recognition failure, and counting it as a miss
understated the model. The benchmark now strips punctuation from both sides before comparing.
The corrected figure is 36/36.

Worth recording because it is the failure mode benchmarks are most prone to: **measuring
something adjacent to what you meant to measure**, and quietly reporting a worse number than
the truth.

---

# 领域词识别基准

**31/36 → 36/36** —— 本地模型在领域词上的表现，纠错层前后对比。

自己跑一遍：

```bash
pip install funasr torch torchaudio numpy
python3 scripts/benchmark_domain_accuracy.py
```

一条命令，无参数。脚本会合成音频、加噪降质、过本地模型，打印出上面那张表。
原始结果：[`benchmark-results.json`](benchmark-results.json)。

## 它测的是什么

十段中文会议语句，每段携带若干通用模型容易听错的领域词——建筑、人事、商务、
平台建设，共 36 个目标词。

**五处失误全部落在工程术语上**，这正是纠错层存在的理由。

## 纠错表是怎么来的

不是靠想象「人会怎么打错」，而是**跑这个基准，把模型真正给出的东西记下来**：

| 想当然会写的 | 模型实际给出的 |
|---|---|
| `旁占` | `航站记录` —— 「航站」是常见词，模型更愿意选它 |
| `危大公程` | `微大工程` —— 声母近似 |
| `塌落度` | `石滩落度` —— 连前面那个字也一起听错了 |

第一次跑这个基准时，**表里原有的 13 条纠错一条都没命中**。它们全是想出来的。
换成五条实测得来的规则后，剩下的失误全部修复。

## 这个基准不是什么

**不是真实场景的准确率。** 音频是 `say` 合成再加粉噪与提速的。这比干净 TTS 难
（不加噪的话模型几乎全对，那样的基准分不出任何东西），但仍然比「四个人、一台投影仪风扇、
还有人开着免提」的真实会议室干净得多。**这个数字是上界，不是现场分数。**

**不是与其他产品的横向对比。** 它测的是这条流水线和它自己。价值在于差值：
换模型、改词库、调参数之后再跑一遍，那个差是可信的，而绝对分数不是。

## 一个值得记下来的度量 bug

第一版直接拿目标词去转写里匹配，得到 35/36。差的那个是 `见证取样送检`，
而模型的转写是 `见证取样，送检` —— **每个字都对**，只是断句逗号插在了词中间。

这是标点位置问题，不是识别错误，把它算作失败是在低估模型。基准现在会在比对前
剥掉两边的标点，修正后的结果是 36/36。

记下来是因为这正是基准最容易犯的错：**测的东西和你想测的东西差了一点点**，
然后安静地报出一个比事实更差的数字。

---

<sub>
<b>JKinco</b> — local-first tools for work whose data cannot leave the building ·
<a href="https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open">Listen</a> ·
<a href="https://github.com/wenxuanzhang1209-cyber/jkinco-slides">Slides</a> ·
<a href="https://github.com/wenxuanzhang1209-cyber/JKinco-Skills-Lab">Skills Lab</a> ·
<a href="https://github.com/wenxuanzhang1209-cyber/personal-life-hub">Life Hub</a> ·
<a href="https://github.com/wenxuanzhang1209-cyber/jkinco-tools">Tools</a>
</sub>
