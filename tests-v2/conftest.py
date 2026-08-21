"""测试套件的统一环境与隔离。

pytest 保证 conftest.py 在任何测试模块之前导入,因此这里是设置环境变量的唯一
正确位置。

为什么必须集中:后端有一批模块级常量在 import 时就固化了配置,例如
`LOGIN_MAX_FAILURES = int(os.getenv("JKINCO_LOGIN_MAX_FAILURES", "5"))`。
谁第一个触发 `import backend.main`,谁当时的环境就决定了整轮测试的取值。
以前靠各测试文件自己 `os.environ.setdefault(...)`,结果是:
  - 全量运行时恰好 test_api.py 字母序在前并设了宽松阈值,于是通过;
  - 只跑其中几个文件时,先导入的文件没设,阈值退回 5,注册接口的每 IP 限流
    在第 6 个用户就开始 429,后续用例拿不到会话,报出与本意无关的 401。
即「测试是否通过取决于文件顺序」。集中到 conftest 之后这一类问题不复存在。

另外:凡是会触发外部调用的配置(钉钉 webhook、大模型端点)一律强制覆盖而非
setdefault —— 开发者 shell 里若载入了真实 .env,setdefault 会保留真实值,
测试就会真的调用大模型(产生费用)或向生产钉钉群推送消息。此事发生过。
"""
from __future__ import annotations

import os
import tempfile

# --- 网络:测试不得真的往外发请求 ---
# 「指向不可达地址」这个意图原先靠 example.invalid 实现,而它在配了代理的机器上
# 并不成立:请求被送去本机代理,代理接受 TCP 连接后吊在 TLS 握手上。实测单次调用
# 从「毫秒级失败」变成打满 10 秒超时,整个套件耗时随之在 440-580 秒之间乱跳,
# 还出现过一次十分钟不结束 —— 当时误判成一次性环境抖动,直到抓栈才看清是代理。
#
# 剥掉代理变量,并把 endpoint 指向必然连接被拒的本机端口:实测 0.9ms 失败,
# 比走代理快一万倍,也让「测试不联网」从约定变成硬保证。
for _proxy_var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FTP_PROXY",
                   "http_proxy", "https_proxy", "all_proxy", "ftp_proxy"):
    os.environ.pop(_proxy_var, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

# --- 外部服务:必须强制覆盖,指向不可达地址 ---
os.environ["LLM_API_KEY"] = "test-key"
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1/chat/completions"
os.environ["LLM_MODEL_NAME"] = "test-model"
# 重试退避在测试里置零。生产默认 2 秒是对的(限流场景下立刻重试只会继续被拒),
# 但每个未被 mock 的 call_llm 失败都要付 2+2 秒纯等待 —— 抓栈发现套件有相当一段
# 时间就停在 time_sleep 上。退避行为本身在 test_llm_retry.py 里用 mock 过的 sleep
# 单独验证,置零不损失覆盖。
os.environ["JKINCO_LLM_RETRY_BACKOFF"] = "0"
os.environ["DINGTALK_WEBHOOK"] = "http://127.0.0.1:1/webhook"
os.environ["DINGTALK_SECRET"] = "test-secret"

# --- 实时会议 ---
os.environ.setdefault("LIVEKIT_API_KEY", "test-api-key")
os.environ.setdefault("LIVEKIT_API_SECRET", "s" * 34)
os.environ.setdefault("LIVEKIT_PUBLIC_URL", "wss://example.invalid/livekit")

# --- 会话与账号 ---
os.environ.setdefault("JKINCO_SESSION_SECRET", "t" * 32)
os.environ.setdefault("JKINCO_AUTH", "admin:123456")

# 注册与登录的每 IP 限流:测试全部来自同一个 "testclient" 地址,会共用计数器。
# 放宽阈值,让限流本身由 test_login_throttle.py 专门覆盖(它直接调用相关函数,
# 不受此处取值影响 —— 那些用例都以 main.LOGIN_MAX_FAILURES 为准动态计算)。
os.environ.setdefault("JKINCO_LOGIN_MAX_FAILURES", "10000")

# --- 数据目录:每轮测试独立,绝不写到真实历史库 ---
_WORK_DIR = tempfile.mkdtemp(prefix="jkinco-tests-")
os.environ.setdefault("JKINCO_HISTORY_DIR", _WORK_DIR)
os.environ.setdefault("JKINCO_UPLOAD_DIR", os.path.join(_WORK_DIR, "uploads"))

import pytest  # noqa: E402  —— 必须在环境变量设置之后


@pytest.fixture(autouse=True)
def _reset_login_throttle():
    """每个用例从干净的限流状态开始。

    失败计数是进程级全局字典,跨文件累积会让后面的用例被无关的限流拦下。
    """
    try:
        from backend.auth import LOGIN_FAILURES, LOGIN_FAILURES_LOCK
    except Exception:  # 不触达后端的测试(纯函数模块)无需处理
        yield
        return
    with LOGIN_FAILURES_LOCK:
        LOGIN_FAILURES.clear()
    yield
    with LOGIN_FAILURES_LOCK:
        LOGIN_FAILURES.clear()


@pytest.fixture(autouse=True)
def _reset_job_queue():
    """每个用例从空的处理队列开始。

    与限流计数同理:处理槽位也是进程级全局状态。多个用例把 EXECUTOR.submit
    打桩成空操作,任务从不执行,于是 finally 里的归还也永远不发生。这种泄漏
    在只有全局上限时被 12 个槽位悄悄吸收,加了按账号配额之后就会让后面的
    用例莫名收到 429 ——「测试是否通过取决于文件顺序」的老问题换了个入口。
    这里在用例后统一复位,任何文件都污染不到下一个。
    """
    try:
        import backend.main as backend_main
    except Exception:  # 不触达后端的测试(纯函数模块)无需处理
        yield
        return
    yield
    backend_main.JOB_SLOTS_BY_USER.clear()
    while True:
        try:
            backend_main.JOB_CAPACITY.release()
        except ValueError:  # BoundedSemaphore 满了就会抛,以此判断已复位到位
            break
