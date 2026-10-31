"""动态路由表：把"上游真正可用的模型"映射成 Claude Desktop 能接受的路由名。

Claude Desktop 的 3P 模式只认 Anthropic 风格的路由名，而上游 opencode go 用的是
自己的模型 id，中间必须有这层映射。几条硬约束（都来自 app.asar 实测）：

1. 路由名必须通过 `Mo()`：不能含厂商词（deepseek/minimax/…），且要含
   claude/sonnet/opus/haiku/fable/mythos/anthropic 之一。picker 会用
   `No("gateway", id)` 把不合格的条目直接过滤掉，条目根本不会出现在列表里。
2. 路由名不能与发布目录里的规范 id 撞名。picker 的标签优先级是
   `labelOverride ?? 目录里的 name ?? 我们的 display_name`；discovery 模式下
   没有 labelOverride，一旦撞名，官方名会盖掉我们的显示名，用户就看不出背后
   到底是哪个模型。撞名判断要用目录 id 的规范形式（去掉日期后缀再比）。
3. 路由名最好是 Claude Code CLI 认识的：陌生 id 会被 CLI 当成 200k 上下文并打
   warning，老名字（claude-opus-4 等）会打 "retired" 警告。
4. 只有 mythos/fable 系列在 Desktop 里能拿到完整 effort 档位（low…max）；
   其它名字只有 thinking 模式。

路由名与上游模型的对应关系存在状态文件里，保证重启后同一个上游模型还是同一个
路由名（否则 Desktop 里记住的选择会漂移）。
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import threading
import time
import urllib.error
import urllib.request

import ccd_config
import ccd_free_scan

STATE_NAME = "route-map.json"
CATALOG_URL = "https://downloads.claude.ai/model-catalog/v1/catalog.json"

# 目录里出现过的规范 id；拿不到线上目录时用它兜底
FALLBACK_CATALOG_NAMES = {
    "claude-fable-5", "claude-fable-5-1", "claude-haiku-4-5", "claude-haiku-5-5",
    "claude-opus-4-1", "claude-opus-4-5", "claude-opus-4-6", "claude-opus-4-7",
    "claude-opus-4-8", "claude-opus-5", "claude-opus-5-5", "claude-sonnet-4-6",
    "claude-sonnet-5", "claude-sonnet-5-5",
}

# app.asar 里 Mo() 用的厂商词黑名单，路由名碰到任何一个都会被 picker 过滤掉
VENDOR_WORDS = re.compile(
    r"ark-code|astron|command-r|deepseek|doubao|gemini|gemma|glm|gpt|grok|hermes"
    r"|hy3|kimi|lfm|\bling\b|llama|longcat|mimo|minimax|mistral|mixtral|moonshot"
    r"|nemotron|openai|phi-|qianfan|qwen|tc-code|\bunic\b|yi-|stepfun|step-3"
    r"|seed-|bytedance|hunyuan|granite|amazon\.nova|nova-|devstral|ministral"
    r"|ernie|codex|arcee|trinity|abab|phi\d|\bk2\.|\bm2\.|jamba|arctic|solar"
    r"|mercury|zamba|kat-coder|\bds-|dpsk",
    re.IGNORECASE,
)
FAMILY_WORDS = ("claude", "sonnet", "opus", "haiku", "fable", "mythos", "anthropic")

# Desktop 的 FC()：去掉 [..]、@日期、-日期后缀，用来和目录 id 比对
_BRACKET_SUFFIX_RE = re.compile(r"\[[^\]]+\]$")
_AT_DATE_RE = re.compile(r"@\d{8}$")
_DATE_SUFFIX_RE = re.compile(r"-\d{8}$")
_BEDROCK_PREFIX_RE = re.compile(r"^arn:aws[a-z-]*:bedrock:[^/]+/")
_ANTHROPIC_PREFIX_RE = re.compile(r"^(?:[a-z][a-z0-9-]*\.)?anthropic\.")
_VERSION_SUFFIX_RE = re.compile(r"-v\d+(?::\d+)?$")
_CLAUDE_LIKE_RE = re.compile(r"^claude-(?:[a-z]+-)?\d")

# 能让 Desktop 给出完整 effort 档位（low…max）的名字族，见 ccd_config.effort_capable
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


def canonical(name: str) -> str:
    """把模型名规范化成可比形式（与 Desktop 的 FC() 语义一致）。

    Desktop/CLI 会把 `claude-opus-4-5` 发成 `claude-opus-4-5-20251101`，
    不规范化就会找不到路由、悄悄回退到第一条路由（用错模型）。
    """
    text = str(name).strip().lower()
    stripped = _BEDROCK_PREFIX_RE.sub("", text)
    stripped = _ANTHROPIC_PREFIX_RE.sub("", stripped)
    claude_like = stripped != text or bool(_CLAUDE_LIKE_RE.match(stripped))
    text = _BRACKET_SUFFIX_RE.sub("", stripped)
    if claude_like:
        text = _VERSION_SUFFIX_RE.sub("", text)
    text = _AT_DATE_RE.sub("", text)
    return _DATE_SUFFIX_RE.sub("", text)


def route_name_ok(name: str) -> bool:
    """路由名是否满足 Desktop 的 `Mo()` 校验（vendor 词 + 家族词）。"""
    text = str(name).strip().lower()
    if not text or VENDOR_WORDS.search(text):
        return False
    return any(word in text for word in FAMILY_WORDS)


def effort_capable(name: str) -> bool:
    """这个路由名在 Desktop 里能不能拿到 effort 档位。

    拿不到 effort 档位时，客户端根本不会带 output_config.effort，
    网关就只能按路由配置强制一个固定值，标签里写死才准确。
    """
    return ccd_config.effort_capable(name)


class RouteTable:
    """当前可用的路由表；带缓存、后台刷新和持久化。"""

    def __init__(self, config: dict):
        self.config = config
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._routes: list[dict] = []
        self._scanned_at = 0.0
        self._catalog_names: set[str] = set(FALLBACK_CATALOG_NAMES)
        self._catalog_fetched_at = 0.0
        self._load_cached()

    # ---- 目录（决定路由名会不会被官方名覆盖） ----------------------
    def catalog_names(self) -> set[str]:
        now = time.time()
        with self._lock:
            fresh = now - self._catalog_fetched_at < 3600
            names = set(self._catalog_names)
        if fresh:
            return names
        fetched = self._fetch_catalog_names()
        if fetched:
            with self._lock:
                self._catalog_names = fetched
                self._catalog_fetched_at = now
            return fetched
        return names

    def _fetch_catalog_names(self) -> set[str]:
        request = urllib.request.Request(CATALOG_URL, headers={
            "accept": "application/json",
            "user-agent": ccd_free_scan.USER_AGENT,
        })
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                document = json.loads(response.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, OSError, ValueError):
            return set()
        names: set[str] = set()
        for surface in (document.get("surfaces") or {}).values():
            for config in surface.get("model_selector_config") or []:
                for model in config.get("models") or []:
                    model_id = model.get("id")
                    if model_id:
                        names.add(canonical(model_id))
        return names

    # ---- 扫描 --------------------------------------------------------
    def scan(self) -> list[dict]:
        """拿到当前上游模型列表。

        默认直接读上游 `/v1/models`，不做逐个探通：一次请求就能拿到完整目录，
        也不会因为某个模型探针超时把本来可用的模型误删。代价是目录里残留的
        下线/地区受限模型也会被列出来，点开才会报错。
        需要逐个验真时把 free_scan.probe 设为 true。
        """
        scan_config = self.config.get("free_scan") or {}
        base_url = self.config["upstream_base_url"]
        key = self.config["upstream_api_key"]
        if scan_config.get("probe"):
            return self._probe_scan(scan_config, base_url, key)

        listed = ccd_free_scan.list_model_ids(
            base_url, key, float(scan_config.get("list_timeout_seconds", 20)))
        families = scan_config.get("protocol_families")
        if families:
            allowed = {str(f).lower() for f in families}
            kept = [m for m in listed if ccd_free_scan.model_family(m) in allowed]
            skipped = len(listed) - len(kept)
            if skipped:
                ccd_config.append_log(
                    f"route list: 上游 {len(listed)} 个模型，跳过 {skipped} 个不支持 "
                    f"Anthropic 协议的厂商（保留 {sorted(allowed)}）")
            listed = kept
        results = [
            {"model": model, "ok": True, "free": "free" in model.lower(),
             "status": "listed", "detail": ""}
            for model in listed
        ]
        # 目录临时抽风时，默认模型和钉住的模型不能消失
        known = {item["model"] for item in results}
        keep = list(self.config.get("pinned_models") or [])
        keep += [route["model"] for route in self.config.get("routes") or []]
        for model in keep:
            if model not in known:
                results.append({"model": model, "ok": True, "free": "free" in model.lower(),
                                "status": "pinned", "detail": ""})
                known.add(model)
        return results

    def _probe_scan(self, scan_config: dict, base_url: str, key: str) -> list[dict]:
        """逐个真发请求验真（慢，但能区分下线/弃用/地区限制）。"""
        candidates, free = ccd_free_scan.candidate_ids(
            base_url,
            key,
            extra=scan_config.get("extra_candidates", []),
            docs_url=scan_config.get("docs_url", ccd_free_scan.DEFAULT_DOCS_URL),
        )
        extra_probe = [route["model"] for route in self.config.get("routes") or []]
        results = ccd_free_scan.scan(
            base_url,
            key,
            candidates,
            extra_probe=extra_probe,
            effort=self.config.get("default_effort", "max"),
            timeout=scan_config.get("probe_timeout_seconds", 25.0),
            workers=scan_config.get("workers", 16),
        )
        for item in results:
            item["free"] = item["model"] in free
        return results

    def refresh(self) -> list[dict]:
        """重新扫描并重建路由表。"""
        results = self.scan()
        routes = self._build_routes(results)
        with self._lock:
            self._routes = routes
            self._scanned_at = time.time()
        save_state({
            "updated_at": self._scanned_at,
            "names": {r["model"]: r["name"] for r in routes},
            "routes": routes,
        })
        return routes

    def ensure_fresh(self) -> list[dict]:
        """缓存过期就重扫；扫描失败时保留旧表，不能把可用模型清空。"""
        scan_config = self.config.get("free_scan") or {}
        max_age = float(scan_config.get("max_age_seconds", 300))
        with self._lock:
            age = time.time() - self._scanned_at
            cached = list(self._routes)
        if cached and age < max_age:
            return cached
        if not self._refresh_lock.acquire(blocking=False):
            # 另一个线程正在扫描，先用旧表顶上
            return cached
        try:
            return self.refresh()
        except Exception as exc:  # 扫描失败不能把可用模型清空
            ccd_config.append_log(f"route scan failed: {type(exc).__name__}: {exc}")
            return cached
        finally:
            self._refresh_lock.release()

    def _load_cached(self) -> None:
        """启动时先载入上次的路由表，上游不可用时选择器也不会空。"""
        state = load_state()
        routes = state.get("routes")
        if isinstance(routes, list) and routes:
            with self._lock:
                self._routes = [r for r in routes if isinstance(r, dict) and r.get("name")]
                self._scanned_at = float(state.get("updated_at") or 0.0)

    # ---- 路由表构建 --------------------------------------------------
    def _build_routes(self, results: list[dict]) -> list[dict]:
        alive = {item["model"] for item in results if item.get("ok")}
        pinned = set(self.config.get("pinned_models") or [])
        previous = load_state().get("names") or {}
        catalog_names = self.catalog_names()
        effort_default = self.config.get("default_effort", "max")

        routes: list[dict] = []
        used_models: set[str] = set()
        used_names: set[str] = set()

        # 1) 已配置的路由：模型还活着就保留；钉住的不管在不在目录里都保留
        for configured in self.config.get("routes") or []:
            model = configured["model"]
            if model not in alive and model not in pinned:
                continue
            name = self._name_for(
                preferred=configured.get("name"),
                model=model,
                used_names=used_names,
                catalog_names=catalog_names,
                remembered=previous.get(model),
            )
            used_models.add(model)
            used_names.add(name)
            routes.append(self._route(
                name=name,
                model=model,
                label=configured.get("label") or self._label(model, effort_default, name),
                effort=configured.get("effort") or effort_default,
                supports1m=bool(configured.get("supports1m", True)),
            ))

        # 2) 上游目录里其余模型全部补进来
        for model in sorted(alive - used_models):
            name = self._name_for(
                preferred=None,
                model=model,
                used_names=used_names,
                catalog_names=catalog_names,
                remembered=previous.get(model),
            )
            used_names.add(name)
            used_models.add(model)
            routes.append(self._route(
                name=name,
                model=model,
                label=self._label(model, effort_default, name),
                effort=effort_default,
                supports1m=True,
            ))

        return routes

    def _name_for(
        self,
        preferred: str | None,
        model: str,
        used_names: set[str],
        catalog_names: set[str],
        remembered: str | None,
    ) -> str:
        """给上游模型挑一个路由名。

        优先级：配置里指定的 → 上次用过的 → 池子里的。任何一个候选只要
        与发布目录撞名，或者已经被别的模型占用，就换下一个。
        """
        candidates: list[str] = []
        if preferred:
            candidates.append(preferred)
        if remembered and remembered not in candidates:
            candidates.append(remembered)
        candidates.extend(self.config.get("route_pool") or ccd_config.DEFAULT_ROUTE_POOL)

        for name in candidates:
            if not route_name_ok(name):
                continue
            if name in used_names:
                continue
            if canonical(name) in catalog_names:
                ccd_config.append_log(
                    f"route name {name!r} collides with the published catalog "
                    f"(model {model}); picking another name")
                continue
            return name

        # 池子用完了，退而求其次给一个仍然合法的名字
        index = 1
        while True:
            name = f"claude-mythos-{index}"
            if name not in used_names:
                return name
            index += 1

    def _label(self, model: str, effort: str, name: str) -> str:
        """显示名 = 上游模型真名；客户端不能选 effort 时把固定值写进标签。"""
        label = ccd_free_scan.pretty_name(model)
        if not effort_capable(name):
            return f"{label} · {effort}"
        return label

    def _route(
        self,
        name: str,
        model: str,
        label: str,
        effort: str,
        supports1m: bool,
    ) -> dict:
        return {
            "name": name,
            "model": model,
            "label": label,
            "effort": effort,
            "supports1m": supports1m,
            # 客户端能选 effort 就听客户端的，选不了就用路由配置的固定值
            "effort_mode": "client" if effort_capable(name) else "fixed",
        }

    # ---- 对外接口 ----------------------------------------------------
    def rows(self) -> list[dict]:
        with self._lock:
            return list(self._routes)

    def lookup(self, model: str) -> dict | None:
        """按请求里的模型名找路由（先精确，再规范化）。"""
        rows = self.rows()
        for route in rows:
            if route["name"] == model:
                return route
        target = canonical(model)
        for route in rows:
            if canonical(route["name"]) == target:
                return route
        return None

    def models_payload(self) -> list[dict]:
        """生成 Anthropic 风格的 /v1/models 数据，交给 Desktop 做 discovery。"""
        payload = []
        for route in self.rows():
            entry = {
                "type": "model",
                "id": route["name"],
                "display_name": route.get("label") or route["name"],
                "description": f"{route['model']} · OpenCode Go",
                "created_at": "2026-01-01T00:00:00Z",
            }
            if route.get("supports1m"):
                entry["supports_1m"] = True
                entry["max_input_tokens"] = int(self.config.get("context_window_tokens", 1000000))
            payload.append(entry)
        return payload


def state_path() -> pathlib.Path:
    return ccd_config.state_dir() / STATE_NAME


def load_state() -> dict:
    path = state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def build_table(config: dict) -> RouteTable:
    return RouteTable(config)


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="ccd_routes", description="查看/刷新动态路由表")
    parser.add_argument("command", choices=["show", "refresh"])
    parser.add_argument("--config", default=None)
    args = parser.parse_args(argv)

    config = ccd_config.load_config(args.config)
    table = build_table(config)
    routes = table.refresh() if args.command == "refresh" else table.ensure_fresh()
    for route in routes:
        print(f"{route['name']:<26} -> {route['model']:<28} {route['label']}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
