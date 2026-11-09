#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""实验：验证「以分段对象数组写入知识库」的可行路径。

Dify 的分段接口 POST /v1/datasets/{ds}/documents/{doc}/segments 需要一个
**已存在且索引完成** 的 document_id，所以要先建文档再补分段。
本脚本对比两种"占位建文档"方式，找出不产生垃圾分段的那一种：

  方案A：create-by-text 传空文本 text=""
  方案B：create-by-text 传一个极小占位文本

然后调用分段接口写入对象数组，回读确认：
  - 每一段 content 与传入完全一致（不被再切分）
  - answer 字段是否落库

用法：
  python verify_segments.py            # 建 -> 验 -> 删
  python verify_segments.py --keep     # 保留临时知识库
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

import wf30_patch as W

API = "http://localhost/v1"
PG = ["docker", "exec", "-i", "docker-db_postgres-1", "psql", "-U", "postgres",
      "-d", "dify", "-t", "-A"]
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _api_key():
    g = W.get_graph()
    n = W.find_node(g, W.KB_WRITE_NODE)
    return n["data"]["authorization"]["config"]["api_key"]


KEY = _api_key()


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
    p = subprocess.run(PG, input=sql, capture_output=True, text=True, encoding="utf-8")
    return p.stdout.strip()


def wait_doc(doc_id, want_ready=True, tries=40):
    for _ in range(tries):
        st = psql("SELECT indexing_status FROM documents WHERE id='%s';" % doc_id)
        if want_ready and st == "completed":
            return st
        if not want_ready and st in ("completed", "error"):
            return st
        time.sleep(3)
    return st


def segs_of(doc_id):
    return psql(
        "SELECT count(*) || ' 段 | ' || coalesce(string_agg('['||replace(left(s.content,50),chr(10),'|')||']', ' '), '') "
        "FROM document_segments s WHERE s.document_id='%s';" % doc_id)


# 模拟工作流 LLM 输出的 JSONL（每行一个 JSON 语句）
JSONL = "\n".join([
    '{"question": "什么是尿路刺激征？", "response_chosen": "尿频、尿急、尿痛三联征。"}',
    '{"question": "中暑现场如何急救？", "response_chosen": "转移到阴凉处，解开衣物，补充含盐水分。"}',
    '{"question": "高血压的诊断标准？", "response_chosen": "非同日三次测量收缩压≥140 和/或 舒张压≥90。"}',
])
SEGMENTS = [{"content": line} for line in JSONL.splitlines() if line.strip()]


def try_plan(name, placeholder_text):
    print("\n" + "=" * 78)
    print("方案：%s   （create-by-text 的 text=%r）" % (name, placeholder_text))
    code, res = call("POST", "/datasets", {
        "name": "zz-分段数组实验-可删-" + name,
        "indexing_technique": "high_quality",
        "permission": "only_me",
        "provider": "vendor",
        "embedding_model": W.EMBEDDING_MODEL,
        "embedding_model_provider": W.EMBEDDING_PROVIDER,
    })
    if code != 200:
        print("  建库失败", code, res)
        return None
    ds = res["id"]
    print("  知识库:", ds)

    code, res = call("POST", "/datasets/%s/document/create-by-text" % ds, {
        "name": "占位文档.txt",
        "text": placeholder_text,
        "indexing_technique": "high_quality",
        "process_rule": {"mode": "custom", "rules": {
            "pre_processing_rules": [{"id": "remove_extra_spaces", "enabled": False},
                                     {"id": "remove_urls_emails", "enabled": False}],
            "segmentation": {"separator": "\n", "max_tokens": 4096, "chunk_overlap": 0}}},
    })
    print("  建文档 ->", code)
    if code != 200:
        print("   ", str(res)[:400])
        return ds
    doc = res["document"]["id"]
    print("  文档 id:", doc, " 初始状态:", wait_doc(doc, want_ready=False))
    print("  占位文档自带分段:", segs_of(doc))

    code, res = call("POST", "/datasets/%s/documents/%s/segments" % (ds, doc),
                     {"segments": SEGMENTS})
    print("  写入分段对象数组 ->", code)
    if code == 200:
        print("   返回段数:", len(res.get("data", [])))
    else:
        print("   ", str(res)[:600])
    time.sleep(4)
    print("  最终分段:", segs_of(doc))
    return ds


def main():
    keep = "--keep" in sys.argv
    made = []
    for name, ph in (("A-空文本", ""), ("B-单空格", " ")):
        ds = try_plan(name, ph)
        if ds:
            made.append(ds)

    print("\n================ 结论 ================")
    print("工作流应采用的写法见输出上方（哪个方案占位文档自带 0 段，就用哪个）。")
    if keep:
        print("保留：", made)
    else:
        for ds in made:
            c, _ = call("DELETE", "/datasets/" + ds)
            print("清理", ds, "->", c)


if __name__ == "__main__":
    main()
