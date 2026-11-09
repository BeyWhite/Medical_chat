#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""实验3：验证「不产生垃圾分段」的分段对象数组写法。

思路：create-by-text 的 text 只放**第一行**（custom 分段 separator=\n，一行=一段），
文档里就有 1 段（=第 1 个 JSON 语句）；索引完成后，再把**第 2..N 行**用
POST /segments 以对象数组写入。最终文档正好 N 段、每段一个 JSON 语句，无需删任何东西。

同时测量 1) 占位文档达到 completed 需要多久（决定 HTTP 节点重试参数）
        2) 建完文档立刻调分段接口会不会 404（判断是否需要重试等待）

用法：python verify_segments3.py <api_key>
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = "http://localhost/v1"
PG = ["docker", "exec", "-i", "docker-db_postgres-1", "psql", "-U", "postgres",
      "-d", "dify", "-t", "-A"]
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
KEY = sys.argv[1]


def call(method, path, payload=None):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + KEY)
    req.add_header("Content-Type", "application/json")
    try:
        with _OPENER.open(req, timeout=120) as r:
            raw = r.read().decode("utf-8")
            return r.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def psql(sql):
    return subprocess.run(PG, input=sql, capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


JSONL = "\n".join([
    '{"question": "什么是尿路刺激征？", "response_chosen": "尿频、尿急、尿痛三联征。"}',
    '{"question": "中暑现场如何急救？", "response_chosen": "转移到阴凉处，解开衣物，补充含盐水分。"}',
    '{"question": "高血压的诊断标准？", "response_chosen": "非同日三次测量收缩压≥140 和/或 舒张压≥90。"}',
])
LINES = [ln for ln in JSONL.splitlines() if ln.strip()]
RULE = {"mode": "custom", "rules": {
    "pre_processing_rules": [{"id": "remove_extra_spaces", "enabled": False},
                             {"id": "remove_urls_emails", "enabled": False}],
    "segmentation": {"separator": "\n", "max_tokens": 4000, "chunk_overlap": 0}}}


def main():
    c, r = call("POST", "/datasets", {
        "name": "zz-实验3-可删", "indexing_technique": "high_quality",
        "permission": "only_me", "provider": "vendor",
        "embedding_model": "Pro/BAAI/bge-m3",
        "embedding_model_provider": "langgenius/siliconflow/siliconflow"})
    ds = r["id"]
    t0 = time.time()
    c, r = call("POST", "/datasets/%s/document/create-by-text" % ds, {
        "name": "知识文档.txt", "text": LINES[0], "indexing_technique": "high_quality",
        "process_rule": RULE})
    doc = r["document"]["id"]
    print("建文档 ->", c, " 用时 %.2fs" % (time.time() - t0))

    # 立刻调分段接口，看是否 404（判断 race）
    c_now, r_now = call("POST", "/datasets/%s/documents/%s/segments" % (ds, doc),
                        {"segments": [{"content": LINES[1]}]})
    print("建完立刻写分段 ->", c_now, str(r_now)[:120])

    st = ""
    while time.time() - t0 < 90:
        st = psql("SELECT indexing_status FROM documents WHERE id='%s';" % doc)
        if st == "completed":
            break
        time.sleep(1)
    print("文档 completed，耗时 %.2fs，当前段数=%s" % (time.time() - t0,
          psql("SELECT count(*) FROM document_segments WHERE document_id='%s';" % doc)))

    # 写上剩余行（若刚才已成功写过第 2 行，则这里只写剩余）
    already = int(psql("SELECT count(*) FROM document_segments WHERE document_id='%s';" % doc))
    todo = LINES[already:]
    if todo:
        c, r = call("POST", "/datasets/%s/documents/%s/segments" % (ds, doc),
                    {"segments": [{"content": x} for x in todo]})
        print("追加分段 ->", c, ("返回 %d 段" % len(r.get("data", []))) if c == 200 else str(r)[:200])
    time.sleep(3)
    print("最终段数:", psql("SELECT count(*) FROM document_segments WHERE document_id='%s';" % doc))
    print("内容:", psql(
        "SELECT string_agg('['||replace(left(content,50),chr(10),'|')||']', ' ') FROM "
        "(SELECT content FROM document_segments WHERE document_id='%s' ORDER BY position) t;" % doc))

    c, _ = call("DELETE", "/datasets/" + ds)
    print("清理 ->", c)


if __name__ == "__main__":
    main()
