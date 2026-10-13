# -*- coding: utf-8 -*-
"""跑一次真实对话，抓出回答里的「引用」（retriever_resources），统计重复情况。

用法：
    python dev/wf50/probe_refs.py "<问题>" <输出json> [连接id或名称关键字]

说明：
    - 默认挑 config.json 里名字含「问答5.0」的连接；找不到就用当前激活连接。
    - SSE 的 `event:` 行是空的（只有 ping 带 name），事件名以 data 里的
      JSON 字段 "event" 为准。
"""
import json
import os
import sys
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))["dify"]


def pick(want):
    apps = CFG.get("apps") or []
    if want:
        for a in apps:
            if a.get("id") == want or want in (a.get("name") or ""):
                return a, a.get("id")
        raise SystemExit("找不到连接：%s（现有：%s）"
                         % (want, [(a.get("id"), a.get("name")) for a in apps]))
    for a in apps:
        if "5.0" in (a.get("name") or ""):
            return a, a.get("id")
    aid = CFG.get("active_app") or ""
    for a in apps:
        if a.get("id") == aid:
            return a, aid
    return {}, "default"


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else "细菌性感染疾病是如何发生的？"
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "refs.json")
    want = sys.argv[3] if len(sys.argv) > 3 else ""
    a, aid = pick(want)
    c = {
        "base_url": (a.get("base_url") or CFG.get("base_url") or "").rstrip("/"),
        "api_key": a.get("api_key") or CFG.get("api_key") or "",
        "name": a.get("name") or aid,
    }
    print("连接：%s（%s）@ %s" % (c["name"], aid, c["base_url"]))

    # 先确认连的是哪个应用
    info = {}
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            c["base_url"] + "/info",
            headers={"Authorization": "Bearer " + c["api_key"]}), timeout=20)
        info = json.loads(r.read().decode("utf-8", "ignore"))
        print("应用：%s / %s" % (info.get("name"), info.get("mode")))
    except Exception as e:
        print("!! /info 失败：%s" % e)

    body = json.dumps({
        "inputs": {},
        "query": query,
        "response_mode": "streaming",
        "conversation_id": "",
        "user": "probe-dedup",
    }).encode("utf-8")
    req = urllib.request.Request(
        c["base_url"] + "/chat-messages", data=body, method="POST",
        headers={"Authorization": "Bearer " + c["api_key"],
                 "Content-Type": "application/json"})
    path = out if out.endswith(".json") else out + ".json"
    resources, answer, ev, meta, err = [], "", {}, {}, None
    with open(path + ".sse", "wb") as f:
        try:
            r = urllib.request.urlopen(req, timeout=900)
        except urllib.error.HTTPError as e:
            print("HTTP", e.code, e.read().decode("utf-8", "ignore")[:400])
            return 2
        for raw in r:
            f.write(raw)
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data: "):
                continue
            try:
                obj = json.loads(line[6:])
            except Exception:
                continue
            name = obj.get("event") or "?"
            ev[name] = ev.get(name, 0) + 1
            if name == "message":
                answer += obj.get("answer", "")
            elif name == "message_end":
                meta = obj.get("metadata") or {}
            elif name == "error":
                err = obj.get("message") or obj
    resources = meta.get("retriever_resources") or []

    def key(r):
        return (r.get("dataset_id"), r.get("document_id"), r.get("segment_id"))

    print("\n事件统计：", ev)
    if err:
        print("!! 错误：", json.dumps(err, ensure_ascii=False)[:300])
    print("答案长度：%d 字" % len(answer))
    print("引用条数：%d" % len(resources))
    print("按 (库,文档,分段) 去重后：%d" % len({key(r) for r in resources}))
    print("按 (库,文档) 去重后：%d" % len({(r.get("dataset_id"), r.get("document_id")) for r in resources}))
    print("\n引用明细：")
    seen = {}
    for r in resources:
        k = key(r)
        seen[k] = seen.get(k, 0) + 1
        print("  - pos=%-3s %-24s | %-30s | seg=%s | score=%s" % (
            r.get("position"), str(r.get("dataset_name"))[:24],
            str(r.get("document_name"))[:30], str(r.get("segment_id"))[:8],
            round(r.get("score") or 0, 4)))
    rep = {k: v for k, v in seen.items() if v > 1}
    print("\n被重复引用的条目：%d 条（重复次数：%s）"
          % (len(rep), sorted(rep.values(), reverse=True)[:10] or "无"))

    json.dump({"query": query, "conn": c["name"], "app": info.get("name") or "",
               "answer": answer, "retriever_resources": resources,
               "events": ev, "error": err},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("已写出：%s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
