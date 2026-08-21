"""模块化架构的整体契约测试（开源本地版）。

后端通过 backend.core 聚合引擎能力。本测试钉死 backend 依赖的全部对外 API：
任何一处导出断裂都会在这里失败，而不是等到运行时才暴露。
"""
import importlib
import os
import tempfile

os.environ.setdefault("JKINCO_HISTORY_DIR", tempfile.mkdtemp(prefix="jkinco-contract-"))
os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("LLM_BASE_URL", "http://127.0.0.1:1/chat/completions")
os.environ.setdefault("LLM_MODEL_NAME", "test-model")
os.environ.setdefault("DINGTALK_WEBHOOK", "http://127.0.0.1:1/webhook")
os.environ.setdefault("DINGTALK_SECRET", "test-secret")

import pytest

from backend import core

# backend/ 与 scripts/ 实际引用的函数(core.xxx)
REQUIRED_FUNCTIONS = [
    "ask_xiaozhi",
    "export_summary_docx",
    "export_summary_pdf",
    "generate_meeting_overview",
    "generate_minutes",
    "infer_app_mode_best_effort",
    "load_meeting_history",
    "mode_label",
    "recorder_dropdown_data",
    "save_meeting_history_record",
    "send_to_dingtalk",
    "should_generate_and_push",
    "should_push_to_dingtalk",
    "transcribe_audio",
    "write_meeting_history",
]

# backend/ 实际引用的常量(core.XXX)
REQUIRED_CONSTANTS = [
    "EXPORT_DIR",
    "HISTORY_DIR",
    "HISTORY_LOCK",
]

# 每个模块的归属：backend.core 的 re-export 必须指向同一个对象
MODULE_OWNERSHIP = {
    "jkinco_scenes": ["mode_label"],
    "jkinco_dingtalk": ["send_to_dingtalk"],
    "jkinco_classifier": ["infer_app_mode_best_effort"],
    "jkinco_devices": ["recorder_dropdown_data"],
    "jkinco_history": ["load_meeting_history", "write_meeting_history", "save_meeting_history_record"],
    "jkinco_assistant": ["ask_xiaozhi"],
    "jkinco_reports": ["generate_minutes", "generate_meeting_overview"],
    "jkinco_export": ["export_summary_docx", "export_summary_pdf"],
    "jkinco_asr": ["transcribe_audio"],
}

# 这些模块由后端/引擎直接 import，不属于 backend.core 的 re-export 面；
# 钉死它们自身仍提供核心能力即可。
MODULE_LOCAL_NAMES = {
    "jkinco_llm": ["call_llm"],
    "jkinco_text": ["clean_markdown_text", "compact_text", "markdown_lines", "redact_secrets"],
    "jkinco_prompts": ["build_minutes_prompt", "build_overview_prompt", "split_text"],
    "jkinco_classifier": ["infer_app_mode", "keyword_score"],
    "jkinco_devices": ["scan_audio_files"],
    "jkinco_scenes": ["output_title", "history_mode_label", "is_talk_mode", "canonical_mode"],
    "jkinco_asr": ["prepare_audio_path", "get_asr_model"],
}


@pytest.mark.parametrize("name", REQUIRED_FUNCTIONS)
def test_backend_required_function_is_available(name):
    assert callable(getattr(core, name, None)), f"backend 依赖的 core.{name} 丢失"


@pytest.mark.parametrize("name", REQUIRED_CONSTANTS)
def test_backend_required_constant_is_available(name):
    assert getattr(core, name, None) is not None, f"backend 依赖的 core.{name} 丢失"


@pytest.mark.parametrize("module_name,names", sorted(MODULE_OWNERSHIP.items()))
def test_reexport_points_at_same_object(module_name, names):
    module = importlib.import_module(module_name)
    for name in names:
        assert hasattr(module, name), f"{module_name} 缺少 {name}"
        assert getattr(core, name) is getattr(module, name), (
            f"core.{name} 与 {module_name}.{name} 不是同一对象,re-export 已断裂"
        )


@pytest.mark.parametrize("module_name,names", sorted(MODULE_LOCAL_NAMES.items()))
def test_module_local_capabilities_are_available(module_name, names):
    module = importlib.import_module(module_name)
    for name in names:
        assert hasattr(module, name), f"{module_name} 缺少 {name}"


def test_asr_model_cache_is_not_reexported():
    """ASR_MODEL 经 global 重绑定，不应被聚合层 re-export。"""
    import jkinco_asr

    assert hasattr(jkinco_asr, "ASR_MODEL"), "ASR_MODEL 应由 jkinco_asr 独占"
    assert not hasattr(core, "ASR_MODEL"), (
        "ASR_MODEL 不应 re-export 到单体:它是 global 重绑定的可变缓存,"
        "re-export 会导致单体持有过期快照"
    )


def _imports_monolith(path) -> bool:
    """是否真的 import 了单体。

    必须用 AST 判定而非文本子串:注释或 docstring 里提到「import JKincoListen」
    (例如解释这段历史的说明)会让子串匹配误报。
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(alias.name == "JKincoListen" for alias in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "JKincoListen":
            return True
    return False


def test_no_module_imports_the_monolith():
    """抽出的模块不得反向依赖单体,否则形成循环导入。"""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = [path.name for path in sorted(root.glob("jkinco_*.py")) if _imports_monolith(path)]
    assert not offenders, f"以下模块反向依赖单体,会造成循环导入: {offenders}"


def test_backend_does_not_import_the_monolith():
    """Web 层不得引入 Gradio 单体。

    单体在模块级会加载 Gradio 全家桶、构建整个 UI、读盘解析全部历史记录、
    扫描录音设备目录并读取 logo。实测把这些拖进 FastAPI 进程要多付 110MB
    常驻内存与 1.3 秒启动,而后端真正需要的能力全部来自 jkinco_* 子模块。
    后端一律经 backend/core.py 取用引擎能力。
    """
    import pathlib

    backend_dir = pathlib.Path(__file__).resolve().parent.parent / "backend"
    offenders = [path.name for path in sorted(backend_dir.glob("*.py")) if _imports_monolith(path)]
    assert not offenders, (
        f"以下后端模块引入了 Gradio 单体,会让生产进程凭空多占上百 MB 内存: {offenders}"
    )


def test_engine_facade_exposes_everything_the_backend_uses():
    """backend/core.py 是 Web 层对引擎的唯一依赖面,必须覆盖所有 core.xxx 调用点。

    否则改动引擎模块时,缺失的名字要到运行时才以 AttributeError 暴露。
    """
    import ast
    import pathlib

    from backend import core

    backend_dir = pathlib.Path(__file__).resolve().parent.parent / "backend"
    referenced: set[str] = set()
    for path in sorted(backend_dir.glob("*.py")):
        if path.name == "core.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "core":
                referenced.add(node.attr)

    missing = sorted(name for name in referenced if not hasattr(core, name))
    assert not missing, f"backend/core.py 缺少后端实际引用的名字: {missing}"
    assert referenced <= set(core.__all__), (
        f"以下名字被引用但未列入 __all__,依赖面会悄悄扩大: {sorted(referenced - set(core.__all__))}"
    )


def test_backend_dependency_graph_is_acyclic():
    """后端模块依赖必须是单向的:路由层 -> 身份/历史层 -> 引擎门面 -> 引擎模块。

    此前 meetings.py 需要 main.py 的 is_admin / read_profile / serialize_history,
    而 main.py 又 import meetings 注册路由,只能靠函数体内延迟导入绕开循环。
    延迟导入能跑通,但把依赖关系藏进了运行时:静态分析看不见、循环一旦成环
    会在某些导入顺序下才炸,而且鼓励继续往 main.py 里堆公共逻辑。
    """
    import ast
    import pathlib

    backend_dir = pathlib.Path(__file__).resolve().parent.parent / "backend"
    edges: dict[str, set[str]] = {}
    for path in sorted(backend_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        deps: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            module = None
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("backend"):
                module = node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("backend."):
                        module = alias.name
            if module:
                tail = module.split(".")[-1]
                if tail != "backend" and tail != path.stem:
                    deps.add(tail)
        edges[path.stem] = deps

    # 深度优先找环
    cycles: list[list[str]] = []

    def walk(node: str, seen: list[str]) -> None:
        for nxt in sorted(edges.get(node, ())):
            if nxt in seen:
                cycles.append(seen[seen.index(nxt):] + [nxt])
            elif nxt in edges:
                walk(nxt, seen + [nxt])

    for start in sorted(edges):
        walk(start, [start])

    assert not cycles, f"后端模块存在循环依赖: {cycles}"


def test_no_lazy_imports_of_backend_modules_inside_functions():
    """后端模块之间不得在函数体内互相导入 —— 那是循环依赖的遮羞布。"""
    import ast
    import pathlib

    backend_dir = pathlib.Path(__file__).resolve().parent.parent / "backend"
    offenders: list[str] = []
    for path in sorted(backend_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.ImportFrom) and (inner.module or "").startswith("backend"):
                    offenders.append(f"{path.name}:{inner.lineno} -> {inner.module}")
    assert not offenders, f"函数体内延迟导入后端模块: {offenders}"


def test_production_code_uses_logging_not_print():
    """生产代码不得用 print 当日志。

    print 的输出没有时间戳、级别和来源模块,在容器日志里与 uvicorn 访问日志混成
    一片,既无法按严重程度过滤也无法定位来源。统一走 jkinco_logging.get_logger。
    (JKincoListen.py 是遗留 Gradio 单体,其 print 属于交互输出,不在此约束内。)
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    targets = sorted(root.glob("jkinco_*.py")) + sorted((root / "backend").glob("*.py"))
    targets.append(root / "report_templates.py")

    offenders: list[str] = []
    for path in targets:
        if not path.exists():
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"以下位置仍用 print 输出日志: {offenders}"


def test_logger_namespace_is_isolated_from_uvicorn():
    """jkinco 日志命名空间必须 propagate=False。

    否则 uvicorn 装在 root 上的 handler 会让每条日志重复打印一次,格式也被接管。
    """
    from jkinco_logging import configure_logging
    import logging

    configure_logging()
    logger = logging.getLogger("jkinco")
    assert logger.propagate is False
    assert logger.handlers, "jkinco 命名空间没有自己的 handler,日志会丢失"
