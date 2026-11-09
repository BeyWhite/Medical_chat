#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按问答7.0「知识检索」节点的真实参数测召回分数。

真实参数：hybrid_search / 向量权重 1 / 关键词 0 / TopK 3 / 阈值 0.5 / 重排序 BAAI/bge-reranker-v2-m3。
（注意：早期报告里的 0.7339 等数字是**关闭重排序**测出来的，与工作流配置不符。）

用法: python dev/score_probe.py [rerank|norerank|all]
"""
import json
import os
import random
import statistics
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
C = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))["dify"]
B = C["base_url"].rstrip("/")
K = C["dataset_api_key"].strip()
DS = C["retrieval_dataset_ids"][0]

urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))


def call(path, body=None, timeout=180):
    r = urllib.request.Request(B + path, method="POST" if body is not None else "GET")
    r.add_header("Authorization", "Bearer " + K)
    d = None
    if body is not None:
        d = json.dumps(body).encode("utf-8")
        r.add_header("Content-Type", "application/json")
    return json.loads(urllib.request.urlopen(r, d, timeout=timeout).read().decode("utf-8"))


def model(rerank=True, threshold=0.5, top_k=3):
    return {
        "search_method": "hybrid_search",
        "reranking_enable": rerank,
        "reranking_mode": "reranking_model",
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
        "top_k": top_k,
        "score_threshold_enabled": threshold is not None,
        "score_threshold": threshold if threshold is not None else 0.0,
    }


def retrieve(q, rerank=True, threshold=0.5, top_k=3):
    recs = call("/datasets/%s/retrieve" % DS,
                {"query": q, "retrieval_model": model(rerank, threshold, top_k)}).get("records") or []
    out = []
    for r in recs:
        seg = r.get("segment") or {}
        out.append((round(r.get("score") or 0, 4), seg.get("id"), (seg.get("content") or "").replace("\n", " ")))
    return out


def q_of(content):
    try:
        return (json.loads(content).get("question") or "").strip()
    except Exception:
        return ""


def load_questions(limit=40):
    """从检索库各文档里取出库内原文问题（问答对的第一问）。"""
    qs = []
    docs = call("/datasets/%s/documents?page=1&limit=50" % DS).get("data") or []
    for dc in docs:
        if len(qs) >= limit:
            break
        segs = call("/datasets/%s/documents/%s/segments?page=1&limit=100"
                    % (DS, dc["id"])).get("data") or []
        for s in segs:
            q = q_of(s.get("content") or "")
            if q:
                qs.append((dc.get("name"), q.split(" / ")[0].strip()))
    return qs


def line(tag, rows):
    if not rows:
        print("   %-34s -> 0 条命中（阈值 0.5）" % tag)
    for sc, _sid, ct in rows:
        print("   %-34s -> %.4f  %s" % (tag, sc, ct[:70]))
        tag = ""


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    print("检索库 %s\n参数 hybrid/向量1/关键词0/TopK3/阈值0.5/rerank=%s\n" % (DS, mode))

    if mode in ("all", "rerank"):
        print("===== A. 自然提问（重排序开，工作流真实参数）=====")
        for q in ["中暑了应该怎样处理？", "高血压患者在饮食上要注意什么？", "肺炎有哪些典型症状？",
                  "心肌梗死的急救措施有哪些？", "中医护理的基本特点是什么？"]:
            print("Q: %s" % q)
            line("  ", retrieve(q))

        print("\n===== B. 非医学问题（看阈值 0.5 是否拦得住）=====")
        for q in ["Python 怎么安装？", "今天股市怎么样？"]:
            print("Q: %s" % q)
            line("  开阈值 ", retrieve(q, threshold=0.5))
            line("  关阈值 ", retrieve(q, threshold=None))

    if mode in ("all", "norerank"):
        print("\n===== C. 自然提问（重排序关，对照）=====")
        for q in ["中暑了应该怎样处理？", "高血压患者在饮食上要注意什么？", "肺炎有哪些典型症状？"]:
            print("Q: %s" % q)
            line("  ", retrieve(q, rerank=False))

    if mode in ("all", "rerank"):
        print("\n===== D. 库内原文问题逐字复述（20 条抽样）=====")
        qs = load_questions(limit=20)
        random.seed(7)
        random.shuffle(qs)
        qs = qs[:20]
        tops = []
        for i, (src, q) in enumerate(qs, 1):
            rows = retrieve(q)
            if rows:
                tops.append(rows[0][0])
                print("  %2d) %.4f  %-46s [%s]" % (i, rows[0][0], q[:46], src[:18]))
                if rows[0][2]:
                    print("        %s" % rows[0][2][:96])
            else:
                print("  %2d) ——      %-46s [%s]" % (i, q[:46], src[:18]))
        if tops:
            print("\n  统计: n=%d  最高 %.4f  中位 %.4f  最低 %.4f  ≥0.9 的有 %d 条"
                  % (len(tops), max(tops), statistics.median(tops), min(tops),
                     sum(1 for t in tops if t >= 0.9)))


if __name__ == "__main__":
    main()
