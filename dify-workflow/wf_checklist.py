#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""离线复刻 Dify 前端「发布前检查清单」(use-checklist.ts) 的核心判定，
用于在不开浏览器的情况下定位 3.0 / 4.0 草稿图里被清单拦下的问题。

用法:
    python wf_checklist.py <draft.json> [<draft.json> ...]
"""
import json
import sys
import re
from collections import defaultdict, deque

START_TYPES = {"start", "trigger-schedule", "trigger-webhook", "trigger-plugin"}
# 有错误分支的节点（sourceHandle 可能是 'fail-branch' / 分支名），连通性判定用全部出边
SPECIAL_SOURCE_HANDLES = {"fail-branch", "error", "false", "true"}


def walk_values(obj, path=""):
    """递归遍历，返回 (path, value)。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_values(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_values(v, f"{path}[{i}]")
    else:
        yield path, obj


def collect_selectors(node_data):
    """收集 data 里的变量选择器：形如 [node_id, var] 或 [node_id, 'output'] 的 2 元字符串数组。"""
    out = []
    for p, v in walk_values(node_data):
        if not isinstance(v, list):
            continue
        if len(v) == 2 and all(isinstance(x, str) for x in v) and v[0]:
            out.append((p, tuple(v)))
    return out


def main():
    for fp in sys.argv[1:]:
        print("=" * 78)
        print("FILE:", fp)
        g = json.load(open(fp, encoding="utf-8"))
        nodes = g.get("nodes", [])
        edges = g.get("edges", [])
        custom = [n for n in nodes if n.get("type") == "custom"]
        # by_id 收录「所有」节点（含 custom-iteration-start 等），避免把迭代内部节点误判为悬空
        by_id = {n["id"]: n for n in nodes if n.get("id")}

        print(f"节点总数={len(nodes)} custom={len(custom)} edges={len(edges)}")
        types = defaultdict(int)
        for n in custom:
            types[n["data"].get("type")] += 1
        print("节点类型:", dict(sorted(types.items())))

        # ---------- 1) 连通性（从 start 可达） ----------
        starts = [n for n in custom if n["data"].get("type") in START_TYPES]
        print("start 节点:", [n["id"] for n in starts])
        adj = defaultdict(list)
        for e in edges:
            adj[e.get("source")].append(e.get("target"))
        reachable = set()
        dq = deque(n["id"] for n in starts)
        while dq:
            cur = dq.popleft()
            if cur in reachable:
                continue
            reachable.add(cur)
            for nxt in adj.get(cur, []):
                if nxt not in reachable:
                    dq.append(nxt)
        unconnected = [n for n in custom if n["id"] not in reachable]
        # 迭代 / 循环容器内的子节点靠 parentId 归属，一并视为已连接
        truly_unconnected = [
            n for n in unconnected
            if not (n.get("parentId") and n["parentId"] in reachable)
        ]
        if truly_unconnected:
            print("\n[问题] 未连接/不可达节点:")
            for n in truly_unconnected:
                print(f"   - {n['id']}  {n['data'].get('type')}  「{n['data'].get('title')}」")
        else:
            print("连通性: OK（所有节点从 start 可达，迭代容器子节点已计入）")

        # ---------- 2) 悬空边 ----------
        print("\n[sanity] 悬空边（source/target 不存在）:")
        dang = 0
        for e in edges:
            if e.get("source") not in by_id or e.get("target") not in by_id:
                dang += 1
                print(f"   - {e.get('id')} {e.get('source')} -> {e.get('target')}")
        if not dang:
            print("   （无）")

        # ---------- 3) 变量选择器指向不存在的节点 ----------
        # 用顺序关系近似：引用的 node_id 必须存在于图中
        print("\n[问题] 变量引用指向不存在的节点:")
        bad = 0
        for n in custom:
            sels = collect_selectors(n["data"])
            for p, (nid, var) in sels:
                if nid in ("sys", "conversation", "env", "environment"):
                    continue
                if nid not in by_id:
                    bad += 1
                    print(f"   - [{n['data'].get('type')}]「{n['data'].get('title')}」 {p} -> {nid}.{var}")
        if not bad:
            print("   （无）")

        # ---------- 4) End 节点输出重名 ----------
        print("\n[问题] End 输出变量重名:")
        seen = defaultdict(list)
        for n in custom:
            if n["data"].get("type") != "end":
                continue
            for o in n["data"].get("outputs", []) or []:
                v = (o.get("variable") or "").strip()
                if v:
                    seen[v].append(n["id"])
        dup = {k: v for k, v in seen.items() if len(v) > 1}
        if dup:
            for k, v in dup.items():
                print(f"   - 「{k}」 出现于 {v}")
        else:
            print("   （无）")

        # ---------- 5) 必备节点 ----------
        print("\n[检查] 必备节点:")
        print("   有 end 节点:", any(n["data"].get("type") == "end" for n in custom))

        # ---------- 6) LLM 节点模型配置 ----------
        print("\n[检查] LLM / 检索 节点模型配置:")
        for n in custom:
            t = n["data"].get("type")
            if t == "llm":
                m = n["data"].get("model") or {}
                print(f"   - llm「{n['data'].get('title')}」 provider={m.get('provider')} name={m.get('name')} mode={m.get('mode')}")
            if t == "knowledge-retrieval":
                print(f"   - kb-retrieval「{n['data'].get('title')}」 datasets={len(n['data'].get('dataset_ids') or [])}")

        # ---------- 7) code 节点 -------------------
        for n in custom:
            if n["data"].get("type") == "code":
                code = n["data"].get("code", "")
                print(f"\n[检查] code「{n['data'].get('title')}」 语言={n['data'].get('code_language')} 行数={len(code.splitlines())}")

        print()


if __name__ == "__main__":
    main()
