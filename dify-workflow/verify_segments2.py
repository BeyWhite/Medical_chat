#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""实验2：找出「以分段对象数组写入知识库」的可行流程。

前置结论（verify_segments.py）：create-by-text 传空/空白文本 -> 文档 0 段 -> 索引 error，
之后分段接口直接 404（Document is not completed）。

本脚本验证两条路线：
  路线C（分段对象数组）：create-by-text 占位 -> 等 completed -> 列出并删除占位分段
                        -> POST /segments 传对象数组 -> 回读校验
  路线D（回退方案）    ：create-by-text 全文 + custom 分段 separator=\n -> 看是否每行一段

用法：python verify_segments2.py
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
KEY = sys.argv[1] if len(sys.argv) > 1 else None


def call(method, path, payload=None):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + KEY)
    req.add_header("Content-Type", "application/json")
    try:
        with _OPENER.open(req, timeout=120) as r:
            raw = r.read().decode("utf-8")
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def psql(sql):
    return subprocess.run(PG, input=sql, capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


def wait_doc(doc_id, tries=50):
    st = ""
    for _ in range(tries):
        st = psql("SELECT indexing_status || '|' || coalesce(error,'') FROM documents WHERE id='%s';" % doc_id)
        if st.split("|")[0] in ("completed", "error"):
            return st
        time.sleep(3)
    return st


def segs_of(doc_id):
    return psql("SELECT count(*) FROM document_segments WHERE document_id='%s';" % doc_id)


JSONL = "\n".join([
    '{"question": "什么是尿路刺激征？", "response_chosen": "尿频、尿急、尿痛三联征。"}',
    '{"question": "中暑现场如何急救？", "response_chosen": "转移到阴凉处，解开衣物，补充含盐水分。"}',
    '{"question": "高血压的诊断标准？", "response_chosen": "非同日三次测量收缩压≥140 和/或 舒张压≥90。"}',
])
SEGMENTS = [{"content": ln} for ln in JSONL.splitlines() if ln.strip()]
RULE_LINE = {"mode": "custom", "rules": {
    "pre_processing_rules": [{"id": "remove_extra_spaces", "enabled": False},
                             {"id": "remove_urls_emails", "enabled": False}],
    "segmentation": {"separator": "\n", "max_tokens": 4000, "chunk_overlap": 0}}}


def new_kb(tag):
    c, r = call("POST", "/datasets", {
        "name": "zz-实验2-" + tag, "indexing_technique": "high_quality",
        "permission": "only_me", "provider": "vendor",
        "embedding_model": "Pro/BAAI/bge-m3",
        "embedding_model_provider": "langgenius/siliconflow/siliconflow"})
    return (r["id"] if c == 200 else None)


def route_c():
    print("\n" + "=" * 78 + "\n路线C：占位建文档 -> 删占位段 -> 写分段对象数组")
    ds = new_kb("C-可删")
    c, r = call("POST", "/datasets/%s/document/create-by-text" % ds, {
        "name": "知识文档.txt", "text": "这是一段用于占位的最小文本内容，稍后会被删除，仅为了让文档索引完成后才能通过分段接口写入真正的知识。", "indexing_technique": "high_quality",
        "process_rule": RULE_LINE})
    doc = r["document"]["id"]
    st = wait_doc(doc)
    print("  占位文档状态:", st, " 段数:", segs_of(doc))
    if st.split("|")[0] != "completed":
        return ds

    c, r = call("GET", "/datasets/%s/documents/%s/segments?limit=100" % (ds, doc))
    ids = [s["id"] for s in r["data"]]
    print("  列出占位分段 ->", c, ids)
    for sid in ids:
        c2, r2 = call("DELETE", "/datasets/%s/documents/%s/segments/%s" % (ds, doc, sid))
        print("    删除", sid[:8], "->", c2)
    time.sleep(3)
    print("  删后段数:", segs_of(doc))

    c, r = call("POST", "/datasets/%s/documents/%s/segments" % (ds, doc), {"segments": SEGMENTS})
    print("  写入分段对象数组 ->", c)
    if c == 200:
        print("    返回段数:", len(r.get("data", [])))
    else:
        print("    ", str(r)[:400])
    time.sleep(3)
    print("  最终段数:", segs_of(doc))
    row = psql("SELECT string_agg(replace(left(content,58),chr(10),'|'), ' || ') FROM "
               "document_segments WHERE document_id='%s';" % doc)
    print("  内容:", row)
    return ds


def route_d():
    print("\n" + "=" * 78 + "\n路线D（回退）：全文 + separator=\\n，看是否每行一段")
    ds = new_kb("D-可删")
    c, r = call("POST", "/datasets/%s/document/create-by-text" % ds, {
        "name": "知识文档.txt", "text": JSONL, "indexing_technique": "high_quality",
        "process_rule": RULE_LINE})
    doc = r["document"]["id"]
    st = wait_doc(doc)
    print("  文档状态:", st, " 段数:", segs_of(doc))
    if st.split("|")[0] == "completed":
        print("  内容:", psql(
            "SELECT string_agg('['||replace(left(content,45),chr(10),'|')||']', ' ') "
            "FROM document_segments WHERE document_id='%s';" % doc))
    return ds


def main():
    made = [route_c(), route_d()]
    for ds in made:
        if ds:
            c, _ = call("DELETE", "/datasets/" + ds)
            print("清理", ds[:8], "->", c)


if __name__ == "__main__":
    main()
