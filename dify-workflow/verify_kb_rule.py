#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""端到端验证「问答3.0」写库节点用的那套参数：Dify 是否接受、分段落盘是否符合预期。

步骤：
  1. POST /v1/datasets      —— 用与工作流完全相同的 body 建一个临时知识库
  2. 回读 datasets.retrieval_model —— 确认向量检索/Rerank/TopK3/阈值0.5
  3. POST /create-by-text   —— 写一段小文本，带自定义分段规则
  4. 回读 document_segments —— 确认分段结果（条数/字数/内容）
  5. DELETE /v1/datasets/{id} —— 清理

用法：
  python verify_kb_rule.py           # 建 -> 验 -> 删
  python verify_kb_rule.py --keep    # 保留临时知识库
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

import wf30_patch as W

API = "http://localhost/v1"


def _api_key():
    """直接从「写入知识库」节点里取密钥，保证与工作流完全一致。"""
    g = W.get_graph()
    n = W.find_node(g, W.KB_WRITE_NODE)
    return n["data"]["authorization"]["config"]["api_key"]


KEY = _api_key()
PG = ["docker", "exec", "-i", "docker-db_postgres-1", "psql", "-U", "postgres",
      "-d", "dify", "-t", "-A"]

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

SAMPLE = (
    "医学知识测试文档\n\n"
    "问：什么是尿路刺激征？      答：尿路刺激征是指尿频、尿急、尿痛   三联征。\n\n"
    "问：中暑现场如何急救？\n答：立即转移到阴凉处，\t\t解开衣物，补充含盐水分，必要时送医。\n\n"
    "问：高血压的诊断标准？答：非同日三次测量收缩压≥140mmHg 和/或 舒张压≥90mmHg。"
)


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


def main():
    keep = "--keep" in sys.argv

    print("【1】POST /v1/datasets （与工作流节点同一份 body）")
    body = W.build_create_kb_payload()
    body["name"] = "zz-分段规格验证-可删"
    code, res = call("POST", "/datasets", body)
    print("    ->", code)
    if code != 200:
        print(res)
        raise SystemExit("建库失败，参数不被接受")
    ds_id = res["id"]
    print("    知识库 id =", ds_id)

    print("\n【2】回读 datasets.retrieval_model")
    rm = psql("SELECT retrieval_model FROM datasets WHERE id='%s';" % ds_id)
    print("   ", rm)
    ok_retrieval = ("semantic_search" in rm and "bge-reranker-v2-m3" in rm
                    and '"top_k": 3' in rm and "0.5" in rm)

    print("\n【3】POST /document/create-by-text （小样本文档，带自定义分段规则）")
    doc_body = {
        "name": "验证文档.txt",
        "text": SAMPLE,
        "indexing_technique": "high_quality",
        "process_rule": W.PROCESS_RULE,
    }
    code, res = call("POST", "/datasets/%s/document/create-by-text" % ds_id, doc_body)
    print("    ->", code)
    if code != 200:
        print(res)
        raise SystemExit("写文档失败")
    doc_id = res["document"]["id"]

    print("\n【4】回读分段结果（等待索引完成）")
    segs = ""
    for _ in range(40):
        segs = psql(
            "SELECT d.indexing_status || ' | seg=' || count(s.id) || ' | ' || "
            "string_agg(left(replace(s.content, chr(10), '⏎'), 60), ' /// ') "
            "FROM documents d LEFT JOIN document_segments s ON s.document_id=d.id "
            "WHERE d.id='%s' GROUP BY d.indexing_status;" % doc_id)
        if "seg=" in segs and "seg=0" not in segs:
            break
        if "error" in segs.lower():
            break
        time.sleep(3)

    print("   ", segs)
    n_seg = 0
    try:
        n_seg = int(segs.split("seg=")[1].split(" ")[0])
    except Exception:
        pass

    print("\n【5】清理")
    if keep:
        print("    保留临时知识库：", ds_id)
    else:
        c2, _ = call("DELETE", "/datasets/" + ds_id)
        print("    DELETE ->", c2)
        left = psql("SELECT count(*) FROM datasets WHERE name LIKE 'zz-%%';")
        print("    残留 zz-* 知识库数 =", left or "0")

    print("\n================ 结论 ================")
    print("检索设置落盘（向量+Rerank+TopK3+0.5）:", "✅" if ok_retrieval else "❌")
    print("分段规则生效（样本被切成 %d 段）      :" % n_seg, "✅" if n_seg >= 2 else "❌")
    print("说明：自定义分段 separator=\\n\\n / 1024 / 50 + 替换连续空格换行制表符")


if __name__ == "__main__":
    main()
