"""测试共用的小工具。"""
from __future__ import annotations

from backend.auth import verify_captcha

# 加数各取 1..8,所以答案落在 2..16
_CAPTCHA_ANSWER_RANGE = range(2, 17)


def solve_captcha(token: str) -> str:
    """求出这个验证码的答案。

    此前 15 个测试文件都写着同一行:
        base64.urlsafe_b64decode(token).decode().split(":", 1)[0]
    ——也就是直接把答案从 token 里读出来。那恰恰是验证码当时的漏洞:答案明文
    写在 token 里,一行 base64 就能拿到。修掉之后这些测试全挂了,这本身就是
    「那个洞是真的」最直接的证据。

    测试要模拟的是「人看图作答」,所以这里穷举 2..16 找出唯一通过的那个 ——
    走的是和真实客户端一样的校验入口,不依赖 token 的内部结构。
    """
    for value in _CAPTCHA_ANSWER_RANGE:
        if verify_captcha(token, str(value)):
            return str(value)
    raise AssertionError("验证码无解 —— 生成或校验逻辑坏了")

    async def recv(self):
        import json
        return json.dumps({"header": {"event": "task-started"}})

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        import asyncio
        import json
        yield json.dumps({
            "header": {"event": "result-generated"},
            "payload": {"output": {"sentence": {
                "text": type(self).sentence_text,
                "sentence_end": True,
                "sentence_id": 1,
                "begin_time": 0,
                "end_time": 1200,
            }}},
        })
        # 保持不断,模拟一场正在进行的转写
        await asyncio.sleep(3600)
