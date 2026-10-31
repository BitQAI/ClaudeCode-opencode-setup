"""扫描 opencode go 上"当前真正可用的免费模型。

为什么要探针：/v1/models 里的条目只是目录，不代表能调通。实测同一个 id 会出现
"Model is unavailable"（模型下线）、410 ModelDeprecated（模型被弃用并给了替代品）、
403 RegionError（地区限制）、400 ModelProtocolUnsupported（不支持 Anthropic 协议）。
所以每个候选都要真发一次最小请求，通不过的一律不算数。

两个踩过的坑：
1. Cloudflare 会拦默认的 Python-urllib User-Agent（403 error code: 1010），
   必须伪装成 claude-cli。
2. 文档页有三张表：model 表（Model | Model ID | ...）、pricing 表（第二列是价格
   "Free"）、deprecation 表。旧正则把 pricing 表的 "Free" 当成 model id，
   现在按表头筛选 model 表。
"""

from __future__ import annotations

import concurrent.futures
import json
import re
import urllib.error
import urllib.request
import uuid

# Cloudflare 只放行看起来像官方 CLI 的 UA
USER_AGENT = "claude-cli/2.1.197 (external, cli)"

DEFAULT_DOCS_URL = "https://opencode.ai/v2/docs/console/models/"

# 曾经是免费模型、现在可能已被替换或下线；留着它们才能第一时间发现"已弃用"
KNOWN_FREE_ALIASES = ("big-pickle", "space-bunny", "space-bunny-free")

VENDOR_NAMES = {
    "claude": "Claude",
    "minimax": "MiniMax",
    "deepseek": "DeepSeek",
    "glm": "GLM",
    "kimi": "Kimi",
    "qwen": "Qwen",
    "gemini": "Gemini",
    "gpt": "GPT",
    "grok": "Grok",
    "mimo": "MiMo",
    "longcat": "LongCat",
    "nemotron": "Nemotron",
    "muse": "Muse",
    "omen": "Omen",
    "jev": "Jev",
    "beam": "Beam",
    "exo": "Exo",
    "flint": "Flint",
    "step": "Step",
    "hy": "HY",
    "big-pickle": "Big Pickle",
    "space-bunny": "Space Bunny",
    "ll-alpha": "LL Alpha",
}

MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*$")
# qwen3.6 / hy3 / glm5 这类把厂商名和版本黏在一起的 id
FUSED_VENDOR_RE = re.compile(r"^([a-z]+)(\d.*)$")

# 实测只有这几个厂商的上游模型真的支持 Anthropic /v1/messages。
# 其余厂商（gpt / gemini / grok / glm / mimo / longcat / hy / muse / omen /
# step / beam）一律返回 400 ModelProtocolUnsupported，进选择器只会让用户
# 点到就报错。这是厂商协议能力，不是临时容量，所以适合静态过滤。
ANTHROPIC_FAMILIES = ("claude", "deepseek", "kimi", "minimax", "qwen")


def model_family(model_id: str) -> str:
    """取上游模型 id 的厂商前缀：qwen3.8-max -> qwen，minimax-m3.1-flash -> minimax。"""
    first = re.split(r"[-_]", model_id.strip().lower(), 1)[0]
    if first in VENDOR_NAMES:
        return first
    fused = FUSED_VENDOR_RE.match(first)
    if fused and fused.group(1) in VENDOR_NAMES:
        return fused.group(1)
    return first


def pretty_name(model_id: str) -> str:
    """把上游 id 变成人读的名字：minimax-m3.1-flash-free -> MiniMax M3.1 Flash Free。"""
    parts = [p for p in re.split(r"[-_]", model_id) if p]
    out: list[str] = []
    for part in parts:
        low = part.lower()
        if low in VENDOR_NAMES:
            out.append(VENDOR_NAMES[low])
            continue
        fused = FUSED_VENDOR_RE.match(low)
        if fused and fused.group(1) in VENDOR_NAMES:
            out.append(f"{VENDOR_NAMES[fused.group(1)]} {part[len(fused.group(1)):]}")
            continue
        out.append(part[:1].upper() + part[1:])
    # claude-haiku-5-5 拆出来的 "5" "5" 要合成 "5.5"
    merged: list[str] = []
    for token in out:
        if merged and token.isdigit() and merged[-1].isdigit():
            merged[-1] = f"{merged[-1]}.{token}"
        else:
            merged.append(token)
    return " ".join(merged)


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html).strip()


def _fetch(url: str, headers: dict, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def list_model_ids(base_url: str, key: str, timeout: float = 20.0) -> list[str]:
    """上游 /v1/models 的 id 列表；拿不到就返回空。"""
    headers = {"x-api-key": key, "accept": "application/json", "user-agent": USER_AGENT}
    try:
        payload = json.loads(_fetch(f"{base_url.rstrip('/')}/v1/models", headers, timeout))
    except (urllib.error.URLError, OSError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []
    return [str(item.get("id")) for item in payload.get("data", []) if item.get("id")]


def list_docs_free_ids(docs_url: str, timeout: float = 20.0) -> list[str]:
    """官方文档 model 表里标注为 Free 的模型 id。

    文档表比 /v1/models 更新更快（刚上线的免费模型常常还没进目录），
    但同一张页面里还有 pricing 表和 deprecation 表，必须按表头挑出 model 表。
    """
    headers = {"accept": "text/html", "user-agent": USER_AGENT}
    try:
        html = _fetch(docs_url, headers, timeout).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError):
        return []
    ids: list[str] = []
    for table in re.findall(r"<table.*?</table>", html, re.S):
        rows = re.findall(r"<tr>(.*?)</tr>", table, re.S)
        if not rows:
            continue
        header = [_strip_tags(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", rows[0], re.S)]
        if len(header) < 2 or header[1] != "Model ID":
            continue
        for row in rows[1:]:
            cells = [_strip_tags(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            if len(cells) < 2:
                continue
            name, model_id = cells[0], cells[1]
            if "free" in name.lower() and MODEL_ID_RE.match(model_id):
                ids.append(model_id)
    return sorted(set(ids))


def candidate_ids(
    base_url: str,
    key: str,
    extra: list[str] | None = None,
    docs_url: str = DEFAULT_DOCS_URL,
    timeout: float = 20.0,
) -> tuple[list[str], set[str]]:
    """所有值得探测的免费模型候选。

    返回 (候选 id 列表, 明确属于"免费"的 id 集合)。
    """
    free: set[str] = set()
    for model_id in list_model_ids(base_url, key, timeout):
        if "free" in model_id.lower():
            free.add(model_id)
    free.update(list_docs_free_ids(docs_url, timeout))
    free.update(m for m in KNOWN_FREE_ALIASES)
    free.update(m for m in (extra or []) if "free" in m.lower() or m in KNOWN_FREE_ALIASES)
    return sorted(free), free


def classify(status: int, body: str) -> str:
    """把上游错误归类，便于区分"下线/弃用/地区限制/协议不兼容"。"""
    if status == 410 or "ModelDeprecated" in body:
        return "deprecated"
    if "RegionError" in body or "not available in your country" in body:
        return "region"
    if "ModelProtocolUnsupported" in body:
        return "protocol"
    if status == 400 and "unavailable" in body.lower():
        return "unavailable"
    if status == 400:
        return "invalid"
    if status in (401, 403):
        return "auth"
    if status >= 500:
        return "server"
    return "error"


def probe_model(
    base_url: str,
    key: str,
    model_id: str,
    effort: str = "max",
    timeout: float = 25.0,
) -> dict:
    """真发一次最小请求，判断这个模型现在能不能用。

    返回 {"model", "ok", "status", "detail"}；ok=False 时 detail 记录原因。
    """
    body = {
        "model": model_id,
        "max_tokens": 4,
        "messages": [{"role": "user", "content": "hi"}],
    }
    if effort:
        body["output_config"] = {"effort": effort}
    headers = {
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "x-opencode-session": f"ccd-scan-{uuid.uuid4()}",
        "user-agent": USER_AGENT,
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
        return {"model": model_id, "ok": True, "status": "ok", "detail": ""}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        return {
            "model": model_id,
            "ok": False,
            "status": classify(exc.code, detail),
            "detail": f"HTTP {exc.code}: {detail}",
        }
    except (urllib.error.URLError, OSError) as exc:
        # 网络抖动不是"模型不可用"，交给上层当作未知处理
        return {
            "model": model_id,
            "ok": False,
            "status": "unknown",
            "detail": f"{type(exc).__name__}: {exc}",
        }


def scan(
    base_url: str,
    key: str,
    candidates: list[str],
    extra_probe: list[str] | None = None,
    effort: str = "max",
    timeout: float = 25.0,
    workers: int = 16,
) -> list[dict]:
    """并发探测所有候选（含额外需要复检的模型，如已配置路由的模型）。"""
    targets = list(dict.fromkeys(list(candidates) + list(extra_probe or ())))
    if not targets:
        return []
    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(probe_model, base_url, key, model_id, effort, timeout): model_id
            for model_id in targets
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:  # 探针自身异常不算可用
                results.append({
                    "model": futures[future],
                    "ok": False,
                    "status": "unknown",
                    "detail": f"{type(exc).__name__}: {exc}",
                })
    results.sort(key=lambda item: item["model"])
    return results


def main(argv: list[str]) -> int:
    import argparse

    import ccd_config

    parser = argparse.ArgumentParser(prog="ccd_free_scan", description="扫描可用的免费模型")
    parser.add_argument("--config", default=None)
    parser.add_argument("--docs-url", default=None)
    args = parser.parse_args(argv)

    config = ccd_config.load_config(args.config)
    scan_config = config.get("free_scan", {})
    base_url = config["upstream_base_url"]
    key = config["upstream_api_key"]
    docs_url = args.docs_url or scan_config.get("docs_url", DEFAULT_DOCS_URL)

    candidates, _free = candidate_ids(
        base_url,
        key,
        extra=scan_config.get("extra_candidates", []),
        docs_url=docs_url,
    )
    current = [route["model"] for route in config["routes"]]
    results = scan(
        base_url,
        key,
        candidates,
        extra_probe=current,
        effort=config.get("default_effort", "max"),
        timeout=scan_config.get("probe_timeout_seconds", 25.0),
        workers=scan_config.get("workers", 16),
    )
    for item in results:
        mark = "OK  " if item["ok"] else "FAIL"
        print(f"{mark} {item['model']:<28} {item['status']:<12} {item['detail'][:90]}")
    working = [item["model"] for item in results if item["ok"]]
    print(f"\n可用 {len(working)}/{len(results)}: {', '.join(working) or '(无)'}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
