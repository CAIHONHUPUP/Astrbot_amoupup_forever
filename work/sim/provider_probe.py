# -*- coding: utf-8 -*-
"""真实模型可用性隔离探测（不读生产配置、不联网外网）。

只做两件事：
1) 看进程环境里有没有常见的 provider 密钥变量（只看名字，不打印值）；
2) 看本机常见推理端口是否在监听（ollama 11434 / lmstudio 1234 / vllm 8000 等），
   并只对 localhost 发一个 /api/tags 或 /v1/models 探测请求。

输出：work/sim/provider_probe.json
"""
from __future__ import annotations

import json
import os
import socket
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "provider_probe.json")

ENV_KEYS = (
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY", "GEMINI_API_KEY", "DASHSCOPE_API_KEY", "MOONSHOT_API_KEY",
    "ZHIPUAI_API_KEY", "SILICONFLOW_API_KEY", "OLLAMA_HOST",
)

PORTS = (
    (11434, "ollama", "/api/tags"),
    (1234, "lmstudio", "/v1/models"),
    (8000, "vllm", "/v1/models"),
    (8080, "local-8080", "/v1/models"),
    (5000, "local-5000", "/v1/models"),
)


def port_open(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.25)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def main():
    env_hits = [name for name in ENV_KEYS if os.environ.get(name)]
    listening = []
    for port, name, path in PORTS:
        if not port_open(port):
            continue
        entry = {"port": port, "name": name, "responds": False}
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=1.0) as resp:
                entry["responds"] = resp.status == 200
                entry["status"] = resp.status
        except Exception as exc:
            entry["error"] = repr(exc)[:200]
        listening.append(entry)

    # ollama 只有 embedding 模型（bge-m3 等）时不算聊天 provider
    chat_models: list[str] = []
    tags_models: list[str] = []
    for item in listening:
        if item.get("name") != "ollama" or not item.get("responds"):
            continue
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1.0) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            for model in payload.get("models") or []:
                name = str(model.get("name") or model.get("model") or "")
                tags_models.append(name)
                details = model.get("details") or {}
                families = [str(x).lower() for x in (details.get("families") or [])]
                family = str(details.get("family") or "").lower()
                if "bert" in families or "bert" in family or "embed" in name.lower():
                    continue
                chat_models.append(name)
        except Exception:  # cleanup: 仿真容错
            tags_models.append("<tags 读取失败>")
    available = bool(env_hits) or bool(chat_models)
    if available:
        summary = "发现候选真实 provider：env=%s；本地端点=%s" % (
            env_hits, [item["name"] for item in listening])
        detail = ("仍需显式授权才能接入：接入会读取密钥/配置并产生外部调用，"
                  "本轮按只读约束未执行，真实语义未覆盖。")
    else:
        if tags_models:
            summary = ("未发现隔离可用的聊天 provider：ollama 在监听，但只有 %s（非聊天模型）；"
                       "无相关环境变量密钥" % "、".join(tags_models))
            detail = ("本轮未读取生产 AstrBot 配置与密钥；ollama 仅 embedding 模型，不能跑分享对话。"
                      "因此真实模型语义未覆盖。")
        else:
            summary = "未发现隔离可用的真实 provider（无相关环境变量，本地常见推理端口未响应）"
            detail = ("本轮未读取生产 AstrBot 配置与密钥；只做环境变量名与 localhost 端口探测。"
                      "因此真实模型语义未覆盖。")

    data = {
        "available": available,
        "env_keys_present": env_hits,
        "local_endpoints": listening,
        "ollama_models": tags_models,
        "chat_models": chat_models,
        "summary": summary,
        "detail": detail,
        "note": "不联网外网、不读生产配置文件；仅 localhost 探测。",
    }
    with open(OUT, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"available": available, "env": env_hits,
                      "endpoints": [item["name"] for item in listening]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
