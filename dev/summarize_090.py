#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 e2e_recall.json / recall_090.json 汇总成「相关度 >= 0.9」清单。

用法：python dev/summarize_090.py
输出：dev/recall_090_清单.md（可读） 与 dev/recall_090_清单.json（结构化）
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def stat(xs):
    xs = sorted(xs)
    if not xs:
        return "n=0"
    mid = xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2
    return "n=%d  最高 %.4f  中位 %.4f  最低 %.4f  >=0.9 %d 条(%.0f%%)" % (
        len(xs), xs[-1], mid, xs[0], sum(1 for x in xs if x >= 0.9),
        100.0 * sum(1 for x in xs if x >= 0.9) / len(xs))


def load_e2e():
    """合并多批端到端结果（按提问去重，先入为主）。"""
    seen, out = set(), []
    for name in ("e2e_recall_batch1.json", "e2e_recall_batch2.json", "e2e_recall.json"):
        for r in load(name) or []:
            q = r.get("q")
            if q and q not in seen:
                seen.add(q)
                out.append(r)
    return out


def main():
    L = []
    W = L.append
    out = {"inner_090": [], "natural_090": [], "e2e_090": [], "e2e_all": []}

    W("# 相关度 ≥ 0.9 的召回数据清单\n")
    W("口径：Dify 知识库检索接口 / 问答7.0 完整对话流；检索参数为工作流真实配置 —— ")
    W("多路召回(multiple)、向量权重 1 / 关键词权重 0、TopK 3、相似度阈值 0.5、")
    W("重排序 BAAI/bge-reranker-v2-m3。分数为检索接口 / retriever_resources 返回的相关度(0~1)。\n")

    # ---------- A. 库内问题原样检索 ----------
    d = load("recall_090.json")
    if d and d.get("inner"):
        rows = [r for r in d["inner"] if (r.get("top") or 0) >= 0.9]
        out["inner_090"] = rows
        allt = [r["top"] for r in d["inner"] if r.get("top") is not None]
        W("\n## A. 库内问题原样检索（逐字复述）\n")
        W("统计：%s\n" % stat(allt))
        W("| # | 测试问题 | 最高相关度 | 命中来源文档 |")
        W("| --- | --- | --- | --- |")
        for i, r in enumerate(sorted(rows, key=lambda x: -x["top"]), 1):
            W("| %d | %s | %.4f | %s |" % (i, r["q"], r["top"], r["doc"]))
        W("")

    # ---------- B. 自然口语提问（单路直检） ----------
    if d and d.get("natural"):
        rows = [r for r in d["natural"] if (r.get("top") or 0) >= 0.9]
        out["natural_090"] = rows
        allt = [r["top"] for r in d["natural"] if r.get("top") is not None]
        W("\n## B. 自然口语提问（单路直检检索接口）\n")
        W("统计：%s\n" % stat(allt))
        W("| # | 测试问题 | 最高相关度 | 命中来源文档 |")
        W("| --- | --- | --- | --- |")
        for i, r in enumerate(sorted(rows, key=lambda x: -x["top"]), 1):
            W("| %d | %s | %.4f | %s |" % (i, r["q"], r["top"], r["doc"]))
        W("")

    # ---------- C. 端到端（完整对话流） ----------
    e = load_e2e()
    if e:
        ok = [r for r in e if not r.get("error")]
        # retriever_resources 的返回顺序不按分数降序 → 逐问取各分段分数，最高值单独统计
        allt, tops = [], []
        for r in ok:
            ss = [s["score"] for s in (r.get("sources") or [])]
            allt += ss
            if ss:
                tops.append(max(ss))
        W("\n## C. 端到端（问答7.0 完整对话流，6 路相似问检索去重后）\n")
        W("逐问最高相关度统计：%s\n" % stat(tops))
        W("全部分段分数统计：%s\n" % stat(allt))
        flat = []
        for r in ok:
            for s in (r.get("sources") or []):
                if s["score"] >= 0.9:
                    flat.append({"q": r["q"], "score": s["score"], "doc": s["doc"],
                                 "content": s["content"][:120]})
        out["e2e_090"] = flat
        out["e2e_tops"] = [{"q": r["q"], "top": max([s["score"] for s in (r.get("sources") or [])] or [0]),
                            "n": len(r.get("sources") or []),
                            "docs": sorted({s["doc"] for s in (r.get("sources") or []) if s["doc"]}),
                            "elapsed": r.get("elapsed")} for r in ok]
        out["e2e_all"] = [{"q": r["q"], "sources": r.get("sources") or [],
                           "elapsed": r.get("elapsed")} for r in ok]
        W("### C-1 逐问最高相关度 ≥ 0.9 的提问\n")
        W("| # | 提问 | 最高相关度 | 命中条数 | 来源文档 |")
        W("| --- | --- | --- | --- | --- |")
        hi = [t for t in out["e2e_tops"] if t["top"] >= 0.9]
        for i, t in enumerate(sorted(hi, key=lambda x: -x["top"]), 1):
            W("| %d | %s | %.4f | %d | %s |"
              % (i, t["q"], t["top"], t["n"], ",".join(t["docs"])))
        W("")
        W("### C-2 全部 ≥0.9 的分段\n")
        W("| # | 提问 | 相关度 | 来源文档 | 分段正文（截断） |")
        W("| --- | --- | --- | --- | --- |")
        for i, r in enumerate(sorted(flat, key=lambda x: -x["score"]), 1):
            W("| %d | %s | %.4f | %s | %s |"
              % (i, r["q"], r["score"], r["doc"], r["content"].replace("|", "/")[:70]))
        W("")
        W("\n### 逐问明细（含 <0.9 与 0 命中）\n")
        W("| 提问 | 命中条数 | 各条相关度 | 来源文档 | 耗时 |")
        W("| --- | --- | --- | --- | --- |")
        for r in ok:
            ss = r.get("sources") or []
            W("| %s | %d | %s | %s | %.1fs |"
              % (r["q"], len(ss), " / ".join("%.4f" % s["score"] for s in ss) or "未命中",
                 ",".join(sorted({s["doc"] for s in ss if s["doc"]})) or "—", r.get("elapsed") or 0))
        W("")

    md = os.path.join(HERE, "recall_090_清单.md")
    open(md, "w", encoding="utf-8").write("\n".join(L))
    js = os.path.join(HERE, "recall_090_清单.json")
    json.dump(out, open(js, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("已写入：\n  %s\n  %s" % (md, js))
    print("A 库内复述 >=0.9: %d 条" % len(out["inner_090"]))
    print("B 自然单路 >=0.9: %d 条" % len(out["natural_090"]))
    print("C 端到端   >=0.9: %d 条" % len(out["e2e_090"]))


if __name__ == "__main__":
    main()
