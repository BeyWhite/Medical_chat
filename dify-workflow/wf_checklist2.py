#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""离线复刻 Dify 1.17 前端 use-checklist.ts 的节点校验（checkValid），
用来复现「发布前检查清单」报出的问题条目。

用法: python wf_checklist2.py <draft.json> [...]
"""
import json
import sys
from collections import defaultdict, deque

START_TYPES = {"start", "trigger-schedule", "trigger-webhook", "trigger-plugin"}
EMPTY_RELATED_OPS = {"empty", "not empty", "is null", "is not null", "exists", "not exists"}
BOOLEAN_VAR_TYPES = {"boolean", "array[boolean]"}


def walk2(obj, path=""):
    """yield 每个 2 元字符串数组（变量选择器）。"""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += walk2(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        if len(obj) == 2 and all(isinstance(x, str) for x in obj):
            out.append((path, tuple(obj)))
        for i, v in enumerate(obj):
            out += walk2(v, f"{path}[{i}]")
    return out


def check(node, datasets):
    """返回该节点的错误消息列表（复刻各节点 checkValid）。"""
    d = node["data"]
    t = d.get("type")
    errs = []

    if t == "answer":
        if not d.get("answer"):
            errs.append("answer: 「回答」为必填")

    elif t == "code":
        vs = d.get("variables", []) or []
        if any(not v.get("variable") for v in vs):
            errs.append("code: 「变量名」为必填")
        if any(not (v.get("value_selector") or []) for v in vs):
            errs.append("code: 「变量值」为必填")
        if not d.get("code"):
            errs.append("code: 「代码」为必填")

    elif t == "document-extractor":
        if not (d.get("variable_selector") or []):
            errs.append("document-extractor: 「输入变量」为必填")

    elif t == "http-request":
        if not d.get("url"):
            errs.append("http-request: 「API」为必填")
        body = d.get("body") or {}
        if body.get("type") == "binary":
            data = body.get("data") or []
            if not data or not (data[0].get("file") if isinstance(data[0], dict) else None):
                errs.append("http-request: 「文件变量」为必填")

    elif t == "iteration":
        if not (d.get("iterator_selector") or []):
            errs.append("iteration: 「输入变量」为必填")
        if not (d.get("output_selector") or []):
            errs.append("iteration: 「输出变量」为必填")

    elif t == "if-else":
        cases = d.get("cases") or []
        if not cases:
            errs.append("if-else: 「IF」为必填")
        for ci, c in enumerate(cases):
            conds = c.get("conditions") or []
            if not conds:
                errs.append(f"if-else: 「{'IF' if ci == 0 else 'ELIF'}」为必填")
            for cond in conds:
                if not (cond.get("variable_selector") or []):
                    errs.append("if-else: 「变量」为必填")
                    continue
                op = cond.get("comparison_operator")
                if not op:
                    errs.append("if-else: 「运算符」为必填")
                    continue
                sub = cond.get("sub_variable_condition")
                if sub:
                    ok = True
                    for cc in sub.get("conditions", []):
                        if not cc.get("comparison_operator"):
                            ok = False
                            break
                        if cc.get("comparison_operator") in EMPTY_RELATED_OPS:
                            continue
                        if cc.get("varType") in BOOLEAN_VAR_TYPES:
                            if cc.get("value") is None:
                                ok = False
                                break
                        elif not cc.get("value"):
                            ok = False
                            break
                    if not ok:
                        errs.append("if-else: 「变量值」为必填")
                else:
                    if op not in EMPTY_RELATED_OPS:
                        vt = cond.get("varType")
                        if vt in BOOLEAN_VAR_TYPES:
                            bad = cond.get("value") is None
                        else:
                            bad = not cond.get("value")
                        if bad:
                            errs.append(
                                "if-else: 「变量值」为必填"
                                f"  <-- sel={cond.get('variable_selector')} op={op!r} varType={vt!r} value={cond.get('value')!r}"
                            )

    elif t == "question-classifier":
        if not (d.get("query_variable_selector") or []):
            errs.append("question-classifier: 「输入变量」为必填")
        if not ((d.get("model") or {}).get("provider")):
            errs.append("question-classifier: 「模型」为必填")
        classes = d.get("classes") or []
        if not classes:
            errs.append("question-classifier: 「分类」为必填")
        elif any(not c.get("name") for c in classes):
            errs.append("question-classifier: 「类别名称」为必填")
        vis = d.get("vision") or {}
        if vis.get("enabled") and not ((vis.get("configs") or {}).get("variable_selector") or []):
            errs.append("question-classifier: 「视觉变量」为必填")

    elif t == "knowledge-retrieval":
        if not (d.get("dataset_ids") or []):
            errs.append("knowledge-retrieval: 「知识库」为必填")
        if d.get("retrieval_mode") == "one-way" and not (
            ((d.get("single_retrieval_config") or {}).get("model") or {}).get("provider")
        ):
            errs.append("knowledge-retrieval: 「系统推理模型」为必填")
        if d.get("retrieval_mode") == "multiple":
            mrc = d.get("multiple_retrieval_config")
            if (mrc or {}).get("reranking_mode") == "reranking_model":
                rm = (mrc or {}).get("reranking_model") or {}
                if not rm.get("provider") or not rm.get("model"):
                    pass  # 仅当 allEconomic&allInternal / allExternal 且未开 rerank 才算错，此处从宽

    elif t == "llm":
        model = d.get("model") or {}
        if not model.get("provider"):
            errs.append("llm: 「模型」为必填")
        if not d.get("memory"):
            pt = d.get("prompt_template") or []
            is_chat = model.get("mode") == "chat"
            if is_chat:
                has = any(
                    (p.get("jinja2_text") or "") if p.get("edition_type") == "jinja2" else (p.get("text") or "")
                    for p in pt
                )
            else:
                p0 = pt if isinstance(pt, dict) else {}
                has = bool(p0.get("jinja2_text")) if p0.get("edition_type") == "jinja2" else bool(p0.get("text"))
            if not has:
                errs.append("llm: 「提示词」为必填")

    elif t == "variable-aggregator":
        if not (d.get("variables") or []):
            errs.append("variable-aggregator: 「变量」为必填")
        else:
            for v in d.get("variables") or []:
                if not v:
                    errs.append("variable-aggregator: 「变量值」为必填")

    return errs


def main():
    for fp in sys.argv[1:]:
        print("=" * 78)
        print("FILE:", fp)
        g = json.load(open(fp, encoding="utf-8"))
        nodes = g.get("nodes", [])
        edges = g.get("edges", [])
        custom = [n for n in nodes if n.get("type") == "custom"]
        by_id = {n["id"]: n for n in custom}

        items = []
        for n in custom:
            errs = check(n, None)
            if errs:
                items.append((n["id"], n["data"].get("type"), n["data"].get("title"), errs))

        print(f"\n>>> 清单条目数 = {len(items)}")
        for i, (nid, ty, title, errs) in enumerate(items, 1):
            print(f"  {i}. [{ty}] {nid} 「{title}」")
            for e in errs:
                print(f"       - {e}")
        print()


if __name__ == "__main__":
    main()
