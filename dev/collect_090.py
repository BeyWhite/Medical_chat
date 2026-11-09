#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""采集「相关度 >= 0.9」的真实召回样本（问答7.0 知识检索节点真实参数）。

真实参数：multiple 多路召回 / 向量权重 1 / 关键词 0 / TopK 3 / 阈值 0.5
          / 重排序 BAAI/bge-reranker-v2-m3。

用法：python dev/collect_090.py [inner|natural|all]
输出：打印明细 + 落盘 dev/recall_090.json
"""
import json
import os
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


_DOCNAME = {}


def doc_names():
    """document_id -> 文档名 映射（/retrieve 不直接返回文档名）。"""
    if _DOCNAME:
        return _DOCNAME
    page = 1
    while page <= 20:
        obj = call("/datasets/%s/documents?page=%d&limit=100" % (DS, page))
        for d in obj.get("data") or []:
            _DOCNAME[d.get("id")] = d.get("name") or ""
        if not obj.get("has_more"):
            break
        page += 1
    return _DOCNAME


def retrieve(q, rerank=True, threshold=0.5, top_k=3):
    recs = call("/datasets/%s/retrieve" % DS,
                {"query": q, "retrieval_model": model(rerank, threshold, top_k)}).get("records") or []
    names = doc_names()
    out = []
    for r in recs:
        seg = r.get("segment") or {}
        did = seg.get("document_id") or ""
        out.append({
            "score": round(r.get("score") or 0, 4),
            "doc": names.get(did, did[:8]),
            "seg_id": seg.get("id") or "",
            "content": (seg.get("content") or "").replace("\n", " "),
        })
    return out


def q_of(content):
    try:
        return (json.loads(content).get("question") or "").strip()
    except Exception:
        return ""


def load_inner(limit=40):
    """库内问答对的原问（跨文档抽样，保持文档多样性）。"""
    qs = []
    docs = call("/datasets/%s/documents?page=1&limit=50" % DS).get("data") or []
    for dc in docs:
        name = dc.get("name") or ""
        segs = call("/datasets/%s/documents/%s/segments?page=1&limit=200"
                    % (DS, dc["id"])).get("data") or []
        for s in segs:
            q = q_of(s.get("content") or "")
            if not q:
                continue
            first = q.split(" / ")[0].strip()
            if 4 <= len(first) <= 40:
                qs.append({"doc": name, "q": first, "all_q": q})
    return qs


NATURAL = [
    # 常见疾病症状及治疗方法.txt
    "感冒了要注意什么？", "怎么预防感冒？", "得了流感要紧吗？", "普通感冒和流感有啥区别？",
    # 内科.docx
    "尿路感染怎么治？", "肾病综合征严重吗？", "缺铁性贫血吃什么？", "贫血怎么调理？",
    # 外科.docx
    "气胸是怎么引起的？", "肋骨断了是什么感觉？", "腹膜炎有什么表现？", "疝气是怎么得的？",
    # 妇产科.txt
    "月经不规律是怎么回事？", "恶露一般多久干净？", "宫外孕危险吗？", "胎盘早剥怎么回事？",
    # 急救.docx
    "中暑了怎么办？", "中暑现场怎么急救？", "怎么判断中暑的严重程度？",
    # 中医护理学核心笔记.txt
    "中医护理的原则", "中医看病有什么特点？", "为什么同样的病治法不一样？",
    # 系统解剖学心得体会+笔记.txt
    "人体有多少块骨头？", "关节由什么构成？", "斜方肌在哪里？", "脊柱有多少块骨头？",
    # 微生物总论部分.docx
    "细菌是怎么传播的？", "病毒感染和细菌感染有什么不同？", "病毒为什么会致病？",
    # 其他常见口语问法
    "高血压吃什么好？", "肺炎有什么症状？", "胃疼吃什么药？", "产后出血如何处理",
    "孩子发烧怎么退烧？", "糖尿病人的饮食禁忌", "骨折现场怎么处理？", "心梗怎么急救？",
    "高血脂怎么调理", "肝硬化的护理要点", "阑尾炎有哪些表现？", "哮喘发作怎么办？",
    "中医护理的原则是什么？", "什么是尿路刺激征？", "连枷胸是什么？", "恶露是什么？",
    "同病异治是什么意思？", "什么是医圣？", "脑颅骨有几块？", "病原菌怎么传播？",
]


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    print("检索库 %s" % DS)
    print("参数 multiple / 向量1 / 关键词0 / TopK3 / 阈值0.5 / rerank BAAI/bge-reranker-v2-m3\n")

    out = os.path.join(HERE, "recall_090.json")
    result = {"inner": [], "natural": []}
    if os.path.exists(out):
        try:
            result.update(json.load(open(out, encoding="utf-8")))
        except Exception:
            pass

    if mode in ("all", "inner"):
        qs = load_inner()
        print("===== A. 库内问题原样检索（逐字复述），共 %d 条候选 =====" % len(qs))
        # 按文档轮转取样，保证跨文档覆盖
        bydoc = {}
        for it in qs:
            bydoc.setdefault(it["doc"], []).append(it)
        order, picked = [], []
        i = 0
        while len(picked) < 40:
            added = False
            for d, items in bydoc.items():
                if i < len(items) and len(picked) < 40:
                    picked.append(items[i])
                    added = True
            if not added:
                break
            i += 1
        hits_09 = []
        for n, it in enumerate(picked, 1):
            rows = retrieve(it["q"])
            top = rows[0]["score"] if rows else None
            rec = {"no": n, "q": it["q"], "doc": it["doc"], "top": top,
                   "n_hit": len(rows), "rows": rows[:3]}
            result["inner"].append(rec)
            flag = "OK" if (top or 0) >= 0.9 else ("--" if top is None else "lo")
            if (top or 0) >= 0.9:
                hits_09.append(rec)
            print("  %2d) %-6s %.4f  %-40s [%s]" % (n, flag, top or 0, it["q"][:40], it["doc"][:20]))
        if result["inner"]:
            tops = [r["top"] for r in result["inner"] if r["top"] is not None]
            print("\n  统计: n=%d  最高 %.4f  中位 %.4f  最低 %.4f  >=0.9 命中 %d 条"
                  % (len(tops), max(tops), statistics.median(tops), min(tops), len(hits_09)))

    if mode in ("all", "natural"):
        print("\n===== B. 自然口语提问（非库内原文），共 %d 条 =====" % len(NATURAL))
        result["natural"] = []
        for n, q in enumerate(NATURAL, 1):
            rows = retrieve(q)
            top = rows[0]["score"] if rows else None
            result["natural"].append({"no": n, "q": q, "top": top, "n_hit": len(rows),
                                      "doc": rows[0]["doc"] if rows else "", "rows": rows[:3]})
            print("  %2d) %-6s %.4f  %-24s [%s]"
                  % (n, "OK" if (top or 0) >= 0.9 else "--", top or 0, q, (rows[0]["doc"] if rows else "")[:22]))
        tops = [r["top"] for r in result["natural"] if r["top"] is not None]
        if tops:
            print("\n  统计: n=%d  最高 %.4f  中位 %.4f  最低 %.4f  >=0.9 命中 %d 条"
                  % (len(tops), max(tops), statistics.median(tops), min(tops),
                     sum(1 for t in tops if t >= 0.9)))

    out = os.path.join(HERE, "recall_090.json")
    json.dump(result, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n已写入 %s" % out)


if __name__ == "__main__":
    main()
