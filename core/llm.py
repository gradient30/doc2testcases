# -*- coding: utf-8 -*-
"""LLM 客户端：OpenAI 兼容 (chat/completions) 与 Anthropic 风格 (/v1/messages) 两种，纯标准库实现。

不落盘任何凭据；调用失败抛 LLMError，由上层决定重试。
"""
from __future__ import annotations

import json
import re
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LLMError(Exception):
    """LLM 调用失败（网络/鉴权/限流/超时/响应异常）。"""


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    api_key: str = Field(min_length=8)
    base_url: str = Field(default="https://open.bigmodel.cn/api/paas/v4")
    model: str = Field(default="glm-4.6")
    style: str = Field(default="openai", pattern="^(openai|anthropic)$")
    temperature: float = Field(default=0.3, ge=0.0, le=1.0)
    timeout_s: int = Field(default=300, ge=10, le=600)
    max_tokens: int = Field(default=8192, ge=1024, le=65536)
    # 限流/网络/5xx/并发占线 自动重试：每 retry_interval_s 秒一次（排队场景等 60s）
    max_api_retries: int = Field(default=30, ge=0, le=120)
    retry_interval_s: int = Field(default=60, ge=1, le=600)
    # 运行时注入的取消探针（不持久化）：重试等待期间响应取消，None 表示不可取消
    abort_check: Any = Field(default=None, exclude=True)


class _RetryableLLMError(LLMError):
    """可重试错误：限流、网络抖动、5xx、网关超时、并发占线（3009）。"""


class AbortedError(LLMError):
    """调用方主动取消（重试等待期间响应 abort_check）。"""


def _is_waf_block(status: int, text: str) -> bool:
    return status in (403, 405) and ("3012" in text or "unusual activity" in text)


def _http_transport(url: str, headers: dict[str, str], payload: dict,
                    timeout: int) -> tuple[int, str]:
    """POST JSON，返回 (status, body_text)。requests 优先；被 WAF 指纹拦截时降级 curl。

    某些网关（zcode.z.ai）按 TLS 指纹拦截 Python 的请求栈，Windows 自带的 curl
    （Schannel TLS）可以通过——降级只在这种场景触发，普通端点永远走 requests。
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    merged = {"Content-Type": "application/json", "User-Agent": "doc2testcases/1.0", **headers}
    try:
        import requests
    except ImportError:
        requests = None  # type: ignore[assignment]

    status: int | None = None
    text = ""
    if requests is not None:
        try:
            r = requests.post(url, data=body, headers=merged, timeout=timeout)
            status, text = r.status_code, r.text
        except Exception:  # noqa: BLE001 网络层异常 -> 换 urllib 再试
            status = None
    if status is None:
        req = urllib.request.Request(url, data=body, headers=merged, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status, text = resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status, text = e.code, e.read().decode("utf-8", "replace")
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            raise _RetryableLLMError(f"网络错误: {e}") from e
    if _is_waf_block(status, text):
        return _curl_post(url, merged, body, timeout)
    return status, text


def _curl_post(url: str, headers: dict[str, str], body: bytes, timeout: int) -> tuple[int, str]:
    import os

    fd, tmp = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "wb") as tf:
            tf.write(body)
        cmd = ["curl", "-sS", "-m", str(timeout), "-X", "POST", url,
               "-o", "-", "-w", "\n__HTTP_STATUS__%{http_code}"]
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
        cmd += ["--data-binary", f"@{tmp}"]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout + 30)
        if proc.returncode != 0:
            raise _RetryableLLMError(f"curl 失败({proc.returncode}): {proc.stderr[:200]}")
        out = proc.stdout
        marker = "\n__HTTP_STATUS__"
        if marker not in out:
            raise LLMError(f"curl 输出异常: {out[:200]}")
        text, status_s = out.rsplit(marker, 1)
        return int(status_s.strip() or 0), text
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _post(url: str, headers: dict[str, str], payload: dict, timeout: int) -> dict:
    status, text = _http_transport(url, headers, payload, timeout)
    try:
        data = json.loads(text) if text else None
    except json.JSONDecodeError:
        data = None
    # 网关业务码封套：{"code":..., "msg":...}（可能包在 HTTP 200 里）
    if isinstance(data, dict) and "code" in data and "msg" in data \
            and not ("choices" in data or "content" in data):
        code = data.get("code")
        if code in (3009, 429, 1005):  # 占线/限流/额度窗口超限：等待重试
            raise _RetryableLLMError(f"code={code}: {data.get('msg', '')}")
        raise LLMError(f"网关业务错误 code={code}: {data.get('msg', '')}")
    if status in (200, 201) and isinstance(data, dict):
        return data
    if _is_waf_block(status, text):
        # 行为限速（unusual activity）：冷却后可过，按可重试处理
        raise _RetryableLLMError(f"网关风控拦截(HTTP {status})，稍后自动重试: {text[:120]}")
    if status in (401, 403):
        raise LLMError(f"鉴权失败 (HTTP {status})：请检查 API Key 是否正确/有效；返回: {text[:120]}")
    if status == 429 or status >= 500:
        raise _RetryableLLMError(f"HTTP {status}: {text[:200]}")
    raise LLMError(f"HTTP {status}: {text[:200]}")


def chat(cfg: LLMConfig, system: str, user: str) -> str:
    """一次对话调用，返回模型文本。限流/网络/5xx 按 retry_interval_s 间隔自动重试。"""
    import sys
    attempt = 0
    while True:
        try:
            return _chat_once(cfg, system, user)
        except _RetryableLLMError as e:
            if cfg.abort_check is not None and cfg.abort_check():
                raise AbortedError("已取消（重试等待期间响应取消请求）") from e
            if attempt >= cfg.max_api_retries:
                raise LLMError(f"重试 {attempt} 次后仍失败: {e}") from e
            attempt += 1
            print(f"[llm] 可重试错误，{cfg.retry_interval_s}s 后第 {attempt}/{cfg.max_api_retries} 次重试: {e}",
                  file=sys.stderr, flush=True)
            time.sleep(cfg.retry_interval_s)


def _chat_once(cfg: LLMConfig, system: str, user: str) -> str:
    base = cfg.base_url.rstrip("/")
    if cfg.style == "anthropic":
        url = f"{base}/v1/messages"
        # 同时带 x-api-key 与 Bearer：标准 Anthropic 只认前者，
        # 部分网关（如 zcode-plan）只认后者，均不影响对方忽略多余的头
        headers = {"content-type": "application/json", "x-api-key": cfg.api_key,
                   "authorization": f"Bearer {cfg.api_key}",
                   "anthropic-version": "2023-06-01"}
        payload: dict[str, Any] = {"model": cfg.model, "max_tokens": cfg.max_tokens,
                                   "temperature": cfg.temperature, "system": system,
                                   "messages": [{"role": "user", "content": user}]}
        data = _post(url, headers, payload, cfg.timeout_s)
        try:
            blocks = data.get("content", [])
            return "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"响应结构异常: {data}") from e
    # 默认 openai 兼容
    url = f"{base}/chat/completions"
    headers = {"content-type": "application/json", "authorization": f"Bearer {cfg.api_key}"}
    payload = {"model": cfg.model, "max_tokens": cfg.max_tokens, "temperature": cfg.temperature,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}]}
    data = _post(url, headers, payload, cfg.timeout_s)
    try:
        return data["choices"][0]["message"]["content"] or ""
    except Exception as e:  # noqa: BLE001
        raise LLMError(f"响应结构异常: {json.dumps(data, ensure_ascii=False)[:300]}") from e


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.+?)```", re.S)


def extract_json(text: str) -> Any:
    """从模型回复中提取 JSON：优先 ```json 围栏，其次首个平衡的 {...}/[...]。"""
    if not text or not text.strip():
        raise LLMError("模型返回为空")
    m = _JSON_FENCE.search(text)
    if m:
        return json.loads(m.group(1).strip())
    t = text.strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start = t.find(opener)
        if start < 0:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return json.loads(t[start:i + 1])
    raise LLMError("模型回复中找不到 JSON")


def chat_json(cfg: LLMConfig, system: str, user: str, *, max_attempts: int = 2) -> Any:
    """chat + JSON 提取；JSON 解析失败带错误反馈重试一次。"""
    last_err: Exception | None = None
    prompt = user
    for _ in range(max_attempts):
        text = chat(cfg, system, prompt)
        try:
            return extract_json(text)
        except (LLMError, json.JSONDecodeError) as e:
            last_err = e
            prompt = user + f"\n\n注意：你上一次的回复无法解析为 JSON（{e}）。请只输出合法 JSON，不要任何解释文字。"
    raise LLMError(f"模型未能返回合法 JSON: {last_err}")
