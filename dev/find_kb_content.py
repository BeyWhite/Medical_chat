#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""在 Dify 的**全部知识库**里检索同一个问题，看内容到底落在哪个库。

用途：问答提示「知识库中未找到相关内容」时，先确认「内容是不是根本不在库里」
      还是「在库里但没被检索到（库没进检索清单 / 阈值太高 / 分段太粗）」。

用法：
    python dev/find_kb_content.py                       # 默认查一氧化碳中毒救治原则
    python dev/find_kb_content.py "中暑的救治原则"
输出：每个库的命中条数、最高分，以及命中正文里是否含给定关键词。
"""
import json
import os
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
BASE = (CFG["dify"]["base_url"] or "http://127.0.0.1/v1").rstrip("/")
KEY = (CFG["dify"]["dataset_api_key"] or "").strip()
RETRIEVAL_IDS = CFG["dify"].get("retrieval_dataset_ids") or []

QUERY = sys.argv[1] if len(sys.argv) > 1 else "一氧化碳中毒的救治原则"
# 想在正文里高亮确认的关键词（可用第 2 个参数覆盖）
MARK = sys.argv[2] if len(sys.argv) > 2 else "救治"

MODEL = {
    "search_method": "semantic_search",
    "reranking_enable": True,
    "reranking_model": {
        "reranking_provider_name": "langgenius/siliconflow/siliconflow",
        "reranking_model_name": "BAAI/bge-reranker-v2-m3",
    },
    "weights": {
        "weight_type": "customized",
        "keyword_setting": {"keyword_weight": 0.0},
        "vector_setting": {
            "vector_weight": 1.0,
            "embedding_model_name": "BAAI/bge-m3",
            "embedding_provider_name": "langgenius/siliconflow/siliconflow",
        },
    },
    "top_k": 5,
    "score_threshold_enabled": True,
    "score_threshold": 0.3,
}


def call(method, path, body=None, timeout=120):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Authorization", "Bearer " + KEY)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def brief(rec):
    """从一条检索结果里抽出可读摘要。"""
    c = rec.get("content") or ""
    try:
        j = json.loads(c)
        q = (j.get("question") or "").replace("\n", " ")
        a = (j.get("response_chosen") or "").replace("\n", " ")
        where = j.get("where") or ""
        return where, q, a
    except Exception:
        return "", c.replace("\n", " ")[:80], c.replace("\n", " ")


def main():
    print("关键词/问题：%s" % QUERY)
    print("检索清单 retrieval_dataset_ids =", RETRIEVAL_IDS)
    print("=" * 78)
    dss = call("GET", "/datasets?page=1&limit=100").get("data") or []
    hit_any = False
    for ds in dss:
        if ds.get("document_count", 0) == 0:
            continue
        in_list = ds["id"] in RETRIEVAL_IDS
        try:
            recs = call("POST", "/datasets/%s/retrieve" % ds["id"],
                        {"query": QUERY, "retrieval_model": MODEL}).get("records") or []
        except Exception as e:
            print("!! %-30s 检索失败：%s" % (ds["name"][:28], e))
            continue
        if not recs:
            continue
        hit_any = True
        top = max((r.get("score") or 0) for r in recs)
        mark = "★检索清单内" if in_list else " "
        print("\n[%s] %s  (%d 篇文档)  命中 %d 条，最高分 %.3f"
              % (mark, ds["name"], ds.get("document_count") or 0, len(recs), top))
        for r in recs:
            where, q, a = brief(r)
            flag = "  <<< 含「%s」" % MARK if MARK and MARK in (q + a) else ""
            print("   %.3f  %s" % (r.get("score") or 0, (q or "")[:70]))
            if flag:
                print("          %s" % (a or "")[:110])
                print("         ", flag.strip())
    if not hit_any:
        print("\n(所有库都没检索到任何内容)")


if __name__ == "__main__":
    main()
