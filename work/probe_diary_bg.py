#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""真机探针（不自动跑）：重载插件后调用面板 detail 接口，轮询到 diary 出现即 PASS。

用法示例：
    python work/probe_diary_bg.py --url http://127.0.0.1:6185 \
        --date 2026-11-16 --start 00:00 --end 01:00

可选：--token（面板鉴权）、--route（默认 /Astrbot_amoupup_forever/schedule_detail）、
--timeout 60、--interval 2。只读接口，不写 plugin_data、不触发重载。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def _post(url, payload, token, timeout=15):
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except ValueError:
        return {"raw": raw}


def main(argv=None):
    parser = argparse.ArgumentParser(description="面板小日记后台链路真机探针")
    parser.add_argument("--url", default="http://127.0.0.1:6185")
    parser.add_argument("--route", default="/Astrbot_amoupup_forever/schedule_detail")
    parser.add_argument("--date", default="")
    parser.add_argument("--start", default="00:00")
    parser.add_argument("--end", default="01:00")
    parser.add_argument("--id", default="")
    parser.add_argument("--token", default="")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args(argv)

    route = args.route if args.route.startswith("/") else "/" + args.route
    url = args.url.rstrip("/") + route
    query = ""
    if args.token:
        query = "?" + urllib.parse.urlencode({"token": args.token})
        url += query
    payload = {"start": args.start, "end": args.end}
    if args.date:
        payload["date"] = args.date
    if args.id:
        payload["id"] = args.id
    print("[probe] POST %s payload=%s" % (url, json.dumps(payload, ensure_ascii=False)))

    started = time.time()
    last = {}
    while time.time() - started < args.timeout:
        try:
            last = _post(url, payload, args.token)
        except (urllib.error.URLError, TimeoutError) as exc:
            print("[probe] %.1fs request failed: %r" % (time.time() - started, exc))
            time.sleep(args.interval)
            continue
        data = last.get("data") if isinstance(last, dict) else None
        data = data if isinstance(data, dict) else {}
        diary = str(data.get("diary") or "").strip()
        status = str(data.get("status") or ("empty" if diary else "no-diary"))
        message = str(data.get("message") or data.get("empty_message") or "")
        print("[probe] %.1fs status=%s diary_len=%d message=%s"
              % (time.time() - started, status, len(diary), message[:60]))
        if diary:
            print("[probe] PASS elapsed=%.1fs status=%s" % (time.time() - started, status))
            return 0
        time.sleep(args.interval)

    print("[probe] FAIL elapsed=%.1fs last=%s"
          % (time.time() - started, json.dumps(last, ensure_ascii=False)[:800]))
    return 1


if __name__ == "__main__":
    sys.exit(main())
