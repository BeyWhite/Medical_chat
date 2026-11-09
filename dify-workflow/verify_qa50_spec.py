#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""实测「问答5.0」的入库规格是否真的落地（不依赖 LLM，直接打 Dify dataset API）：

  1. 用工作流「确保知识库存在」节点里那份 body 建库
     -> 回读：indexing_technique / embedding_model / retrieval_model
        （验证 chunk_overlap 之外的检索规格确实落盘到 dataset）
  2. 用工作流「写入问答对txt」节点里那份 process_rule 写文档
     -> 回读 dataset_process_rules：确认 custom / 重叠 50 / remove_extra_spaces 生效
     -> 回读分段：确认「一行一个 JSON = 一段」，并且**没有因为重叠 50 而产生重复内容**
  3. 故意塞入「连续空格 / 连续换行 / 制表符」，确认预处理确实把它们规整掉、
     而且不会破坏行结构（这是 remove_extra_spaces 与"一行一段"是否冲突的关键验证）
  4. 清理

用法：python verify_qa50_spec.py
"""
import json
import random
import string
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, ".")
import wf50_patch3 as p3

API = "http://localhost/v1"
PG = ["docker", "exec", "-i", "docker-db_postgres-1", "psql", "-U", "postgres",
      "-d", "dify", "-t", "-A"]
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_g = p3.base.get_graph()
KEY = p3.base.find_node(_g, p3.N_ADD_DOC)["data"]["authorization"]["config"]["api_key"]

# 5 行 JSON（一行一条），行内故意混入连续空格；行间混入多余空行
MESSY = (
    '{"question": "什么是先兆中暑？",    "response_chosen": "头晕、口渴、多汗，体温正常或略升高。"}\n'
    '\n'
    '\n'
    '\n'
    '{"question": "轻症中暑体温多少度？",  "response_chosen": "体温升至 38℃ 以上，面色潮红。"}\n'
    '{"question": "重症中暑包括哪几种？",   "response_chosen": "热痉挛、热衰竭、热射病。"}\n'
    '\t\t\n'
    '{"question": "热射病的典型表现是什么？", "response_chosen": "体温可达 40℃ 以上，意识障碍甚至昏迷。"}\n'
    '{"question": "中暑急救有哪些禁忌？",    "response_chosen": "意识不清时不要强行喂水。"}\n'
)
EXPECT_LINES = 5


def call(method, path, payload=None):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + KEY)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with _OPENER.open(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")[:400]


def psql(sql):
    return subprocess.run(PG + ["-c", sql], capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


def main():
    print("节点里的 API key:", KEY[:14] + "...")
    ok = {}

    # 1) 建库：直接用节点 body，只把文件名换成临时随机名
    body = p3.build_create_body()
    name = "规格实测-" + "".join(random.choices(string.ascii_lowercase, k=6))
    body["name"] = name
    c, r = call("POST", "/datasets", body)
    print("\n=== 1) 建库（用节点的 body） ===")
    print("  HTTP", c)
    if c != 200:
        print("  返回:", r)
        raise SystemExit(1)
    ds = r["id"]

    # 回读 dataset
    row = psql("SELECT indexing_technique||' | '||embedding_model||' | '||embedding_model_provider||' | '||"
               "coalesce(retrieval_model::text,'-') FROM datasets WHERE id='%s';" % ds)
    print("  回读 dataset:", row[:400])
    ok["索引高质量"] = "high_quality" in row
    ok["嵌入=bge-m3"] = p3.EMBED_MODEL in row
    ok["检索规格落盘"] = ('semantic_search' in row and p3.RERANK_MODEL in row
                     and '"top_k": 3' in row and '0.5' in row)
    for k, v in (("索引高质量", ok["索引高质量"]), ("嵌入=bge-m3", ok["嵌入=bge-m3"]),
                 ("检索规格落盘", ok["检索规格落盘"])):
        print("    %-12s %s" % (k, "✅" if v else "❌"))

    # 2) 写文档：用节点的 process_rule
    add_body = p3.build_add_doc_body()
    add_body["name"] = "规格实测.txt"
    add_body["text"] = MESSY
    c, r = call("POST", "/datasets/%s/document/create-by-text" % ds, add_body)
    print("\n=== 2) 写文档（用节点的 process_rule） ===")
    print("  HTTP", c)
    if c != 200:
        print("  返回:", r)
        psql("DELETE FROM datasets WHERE id='%s';" % ds)
        raise SystemExit(1)

    for _ in range(45):
        st = psql("SELECT indexing_status FROM documents WHERE dataset_id='%s' LIMIT 1;" % ds)
        if st == "completed":
            break
        time.sleep(2)
    print("  文档状态:", psql("SELECT indexing_status FROM documents WHERE dataset_id='%s';" % ds))

    rule = psql("SELECT mode||' | '||coalesce(rules::text,'-') FROM dataset_process_rules "
                "WHERE dataset_id='%s' LIMIT 1;" % ds)
    print("  回读分段规则:", rule[:400])
    ok["规则落盘=custom"] = ("custom" in rule)
    ok["重叠=50"] = ('"chunk_overlap": 50' in rule or '"chunk_overlap":50' in rule)
    # 库里存的 JSON 压缩后无空格，做一次空格归一化再匹配
    _rule_flat = rule.replace(" ", "")
    ok["预处理=开"] = ('"id":"remove_extra_spaces","enabled":true' in _rule_flat)

    rows = psql("SELECT position||' :: '||replace(content, chr(10), '\\\\n') "
                "FROM document_segments WHERE dataset_id='%s' ORDER BY position;" % ds)
    segs = []
    for ln in rows.splitlines():
        if " :: " in ln:
            _, _, c2 = ln.partition(" :: ")
            segs.append(c2.strip())
    print("\n=== 3) 分段结果 === 共 %d 段（期望 %d 段）" % (len(segs), EXPECT_LINES))
    for s in segs:
        print("   ·", s[:120])
    ok["分段数=行数"] = (len(segs) == EXPECT_LINES)
    ok["每段完整JSON"] = bool(segs) and all(s.startswith("{") and s.endswith("}") for s in segs)
    # 重叠是否导致内容重复：所有段拼起来里不应出现重复的 question 文本
    joined = "".join(segs)
    qs = [s.split('"question"')[1][:40] for s in segs if '"question"' in s]
    ok["无重叠重复"] = (len(qs) == len(set(qs)) and joined.count('"response_chosen"') == EXPECT_LINES)
    # 预处理是否生效：段内不应再有 2+ 连续空格，也不应有残留空段
    ok["预处理生效"] = all("  " not in s for s in segs) and all(s.strip() for s in segs)
    for k in ("分段数=行数", "每段完整JSON", "无重叠重复", "预处理生效"):
        print("    %-12s %s" % (k, "✅" if ok[k] else "❌"))

    print("\n    规则明细检查:")
    for k in ("规则落盘=custom", "重叠=50", "预处理=开"):
        print("      %-14s %s" % (k, "✅" if ok[k] else "❌"))

    psql("DELETE FROM datasets WHERE id='%s';" % ds)
    print("\n已清理测试知识库")

    print("\n=== 汇总 ===")
    for k, v in ok.items():
        print("  %-14s %s" % (k, "✅ 通过" if v else "❌ 失败"))
    raise SystemExit(0 if all(ok.values()) else 1)


if __name__ == "__main__":
    main()
