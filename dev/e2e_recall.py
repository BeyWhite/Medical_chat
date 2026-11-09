#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""端到端采集：走问答7.0 完整对话流，取系统实际返回的召回分数。

与 collect_090.py 的区别：
  collect_090.py  —— 单条 query 直接打检索接口（静态口径）
  本脚本          —— 完整工作流（原问 + 5 相似问，6 路并行检索 → 去重 → 返回），
                     收集 retriever_resources 里系统实际给出的相关度分数。

用法：python dev/e2e_recall.py [问题1] [问题2] ...
     不带参数时跑内置问题集。
输出：dev/e2e_recall.json
"""
import json
import os
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))["dify"]
ACTIVE = CFG.get("active_app")
APP = None
for a in CFG.get("apps") or []:
    if a.get("id") == ACTIVE:
        APP = a
if not APP:
    APP = {"api_key": CFG["api_key"], "base_url": CFG["base_url"], "name": "?"}
B = APP["base_url"].rstrip("/")
K = APP["api_key"].strip()

urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

QUESTIONS = [
    "感冒了要注意什么？",
    "气胸是怎么引起的？",
    "脑颅骨有几块？",
    "病原菌怎么传播？",
    "什么是尿路刺激征？",
    "连枷胸是什么？",
    "产后出血如何处理",
    "腹膜炎有什么表现？",
    "高血压吃什么好？",
    "中暑现场怎么急救？",
    "病毒感染和细菌感染有什么不同？",
    "脊柱有多少块骨头？",
    "得了流感要紧吗？",
    "缺铁性贫血吃什么？",
    "糖尿病人的饮食禁忌",
]


def ask(q, timeout=300):
    body = {"inputs": {}, "query": q, "response_mode": "blocking",
            "user": "probe-%d" % int(time.time() * 1000 % 10 ** 9)}
    req = urllib.request.Request(B + "/chat-messages", method="POST")
    req.add_header("Authorization", "Bearer " + K)
    req.add_header("Content-Type", "application/json")
    t0 = time.time()
    obj = json.loads(urllib.request.urlopen(req, json.dumps(body).encode("utf-8"),
                                            timeout=timeout).read().decode("utf-8"))
    dt = time.time() - t0
    meta = obj.get("metadata") or {}
    srcs = meta.get("retriever_resources")
    if srcs is None:
        srcs = (obj.get("data") or {}).get("retriever_resources") or []
    out = []
    for r in srcs or []:
        out.append({
            "score": round(r.get("score") or 0, 4),
            "doc": r.get("document_name") or "",
            "seg": r.get("position"),
            "content": (r.get("content") or "").strip().replace("\n", " "),
        })
    return {"answer": (obj.get("answer") or "")[:400], "elapsed": round(dt, 1),
            "n": len(out), "sources": out, "usage": meta.get("usage")}


def main():
    qs = sys.argv[1:] or QUESTIONS
    print("应用：%s  %s" % (APP.get("name"), B))
    print("问题数：%d\n" % len(qs))
    result = []
    for n, q in enumerate(qs, 1):
        try:
            r = ask(q)
        except Exception as e:
            print("  %2d) [ERR] %s -> %s" % (n, q, str(e)[:120]))
            result.append({"no": n, "q": q, "error": str(e)[:200]})
            continue
        top = r["sources"][0]["score"] if r["sources"] else None
        docs = []
        for s in r["sources"]:
            if s["doc"] and s["doc"] not in docs:
                docs.append(s["doc"])
        print("  %2d) 命中 %d 条  最高 %.4f  %5.1fs  %-22s [%s]"
              % (n, r["n"], top or 0, r["elapsed"], q, ",".join(docs)[:44]))
        for s in r["sources"][:5]:
            print("        %.4f  %s" % (s["score"], s["doc"]))
        result.append(dict({"no": n, "q": q, **r}))
    out = os.path.join(HERE, "e2e_recall.json")
    json.dump(result, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n已写入 %s" % out)


if __name__ == "__main__":
    main()
