"""Localize report prose without translating input names or execution evidence."""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache, wraps
from pathlib import Path
from typing import Any, Iterator

_language: ContextVar[str] = ContextVar("dockstart_report_language", default="zh-CN")
SUPPORTED_REPORT_LANGUAGES = ("zh-CN", "en-US")


def get_report_language() -> str:
    return _language.get()


@contextmanager
def report_locale(language: str | None = None) -> Iterator[str]:
    selected = get_report_language() if language is None else language
    if selected not in SUPPORTED_REPORT_LANGUAGES:
        raise ValueError("报告语言必须为 zh-CN 或 en-US。")
    token = _language.set(selected)
    try:
        yield selected
    finally:
        _language.reset(token)


def localized_report(function):
    """Accept an optional report_language keyword; nested builders inherit it."""
    @wraps(function)
    def wrapped(*args, report_language: str | None = None, **kwargs):
        if report_language is not None and report_language not in SUPPORTED_REPORT_LANGUAGES:
            return {
                "ok": False,
                "message": "报告语言无效。",
                "error": {
                    "code": "REPORT_LANGUAGE_INVALID",
                    "message": "报告语言必须为 zh-CN 或 en-US。",
                    "suggestion": "请选择中文或 English 后重新生成报告。",
                },
            }
        with report_locale(report_language) as selected:
            result = function(*args, **kwargs)
            if isinstance(result, dict) and result.get("ok") and (
                "report_text" in result or "report_file" in result
            ):
                result["report_language"] = selected
            return result
    return wrapped


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


@lru_cache(maxsize=1)
def _messages() -> dict[str, str]:
    return json.loads(Path(__file__).with_name("report_messages_en.json").read_text(encoding="utf-8"))


def _interpolate(message: str, values: tuple[Any, ...]) -> str:
    # One pass: braces and Chinese text inside captured user data stay verbatim.
    return re.sub(r"\{(\d+)\}", lambda match: str(values[int(match[1])])
                  if int(match[1]) < len(values) else match[0], message)


@lru_cache(maxsize=1)
def _patterns():
    patterns = []
    for source, target in sorted(_messages().items(), key=lambda pair: len(pair[0]), reverse=True):
        positions = []
        offset = 0
        parts = []
        for match in re.finditer(r"\{(\d+)\}", source):
            parts.append(re.escape(source[offset:match.start()]).replace(r"\ ", r"\s+"))
            parts.append("(.*?)")
            positions.append(int(match[1]))
            offset = match.end()
        if positions:
            parts.append(re.escape(source[offset:]).replace(r"\ ", r"\s+"))
            patterns.append((source, re.compile("^" + "".join(parts) + "$", re.DOTALL), target, positions))
    return patterns


def rt(message: Any, *values: Any) -> Any:
    """Only call on generated labels/prose, never on user data or raw evidence."""
    if not isinstance(message, str):
        return message
    if get_report_language() == "zh-CN":
        return _interpolate(message, values) if values else message
    source = _normalize(message)
    target = _messages().get(source)
    if target is None and not values:
        if "。；" in message:
            parts = re.split(r"(?<=。)；", message)
            return "; ".join(rt(part) for part in parts)
        # Persisted, generated review messages may contain paths as placeholders.
        for pattern_source, expression, translation, positions in _patterns():
            match = expression.fullmatch(message.strip())
            if match:
                captures = [""] * (max(positions) + 1)
                for index, position in enumerate(positions):
                    captures[position] = match[index + 1]
                generated_subjects = {
                    "当前文件未包含足够化学信息；需要原始 PDB/mmCIF 才能检查{0}。": (0,),
                    "未检测到{0}记录。": (0,),
                    "检测到 {0} {1}；请人工决定保留或处理策略。": (1,),
                }
                for position in generated_subjects.get(pattern_source, ()):
                    captures[position] = rt(captures[position])
                target = _interpolate(translation, tuple(captures))
                break
    if target is None:
        return _interpolate(message, values) if values else message
    leading = re.match(r"\s*", message)[0]
    trailing = re.search(r"\s*$", message)[0]
    translated = leading + target.strip() + trailing
    return _interpolate(translated, values) if values else translated
