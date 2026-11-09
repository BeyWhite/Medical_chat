#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""「问答5.0」第四阶段：把「去重」节点真正接上，去掉重复的引用。

背景（源码级确认，见对话记录）：
  - 已发布版本里是 `循环检索知识库回答(迭代) -> 汇总作答(LLM)`，**没有去重环节**；
    草稿里那个名为「去重」的代码节点是**空壳**（默认代码 `arg1+arg2`、变量选择器为空），
    而且汇总作答的 context 仍直接指向 `node_iteration.output`，等于没接。
  - 迭代 = 5 个相似问 × 每个 TopK 3 = 最多 15 条检索结果，彼此大量重合。
  - graphon 的 LLM 节点 `_build_array_context_event()` 会把数组里**每一条**都塞进
    retriever_resources（不去重）；而引用去重只发生在 core/app 的
    `message_cycle_manager.handle_retriever_resources()`，它按 (dataset_id, document_id)
    去重、且 **existing_ids 在循环内不更新** → **同一个事件内部的重复过滤不掉**。
    知识检索节点本身不上报引用事件 → 15 条全由汇总作答节点一次性上报
    → 页面上就出现重复的引用。所以必须在 LLM 之前把数组去重。

改动：
  1. 「去重」(1791874141296) 填入真实代码：按 segment_id / (文档+正文) / 正文 去重，
     同一条只留分最高的一份，保序；顺带丢掉 content 为空或非字符串的条目
     （这类条目会让 LLM 节点直接抛 InvalidContextStructureError）。
     输入变量 items <- node_iteration.output；输出 result 类型 array[object]。
  2. 「汇总作答」(node_llm_final) 的 context 由 node_iteration.output
     改为 1791874141296.result。
  3. 连线不变（迭代 -> 去重 -> 汇总作答 已经在草稿里）。

命令（全部可回退）：
  python wf50_patch4.py check                # 看现状
  python wf50_patch4.py selftest             # 用合成数据跑一遍去重代码（不碰库）
  python wf50_patch4.py apply [--publish]    # 写草稿（--publish 才生效到运行时）
  python wf50_patch4.py rollback <快照名>
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wf50_patch2 as base  # noqa: E402  复用 psql / 快照 / 发布 / 图校验

APP_ID = base.APP_ID
APP_NAME = base.APP_NAME
BACKUP_DIR = base.BACKUP_DIR

N_DEDUP = "1791874141296"        # 去重（代码节点）
N_ITER = "node_iteration"        # 循环检索知识库回答
N_FINAL = "node_llm_final"       # 汇总作答

# ---------------------------------------------------------------- 去重代码
DEDUP_CODE = '''def main(items) -> dict:
    """按「分段」去掉重复的检索结果，让引用与上下文都不再重复。

    items: 迭代节点 flatten 后的检索结果数组，每项形如
           {"content": ..., "title": ..., "files": ...,
            "metadata": {"_source": "knowledge", "dataset_id": ...,
                         "document_id": ..., "segment_id": ..., "score": ...}}
    返回:  {"result": [去重后的检索结果]}
    """
    if items is None:
        items = []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return {"result": []}

    kept = []      # 保序保留的结果
    slot = {}      # 去重键 -> kept 下标
    texts = set()  # 已保留的正文指纹（正文完全一样也视为重复）

    for it in items:
        if not isinstance(it, dict):
            continue
        content = it.get("content")
        if not isinstance(content, str):
            continue
        content = content.strip()
        if not content:
            continue

        meta = it.get("metadata")
        if not isinstance(meta, dict):
            meta = {}

        text_key = " ".join(content.split())
        ds = str(meta.get("dataset_id") or "")
        seg = str(meta.get("segment_id") or "")
        doc = str(meta.get("document_id") or "")

        if seg:
            key = "seg|%s|%s" % (ds, seg)
        elif doc:
            key = "doc|%s|%s|%s" % (ds, doc, text_key[:200])
        else:
            key = "txt|%s" % text_key[:200]

        try:
            score = float(meta.get("score") or 0)
        except Exception:
            score = 0.0

        pos = slot.get(key)
        if pos is not None:
            # 同一条被多次召回 -> 只留分最高的那份
            old = kept[pos]
            old_meta = old.get("metadata")
            try:
                old_score = float((old_meta or {}).get("score") or 0)
            except Exception:
                old_score = 0.0
            if score > old_score:
                kept[pos] = it
            continue

        if text_key in texts:
            continue

        texts.add(text_key)
        slot[key] = len(kept)
        kept.append(it)

    return {"result": kept}
'''


# ---------------------------------------------------------------- 工具
def _walk(g):
    return {n["id"]: n for n in g["nodes"]}


def describe():
    g = base.get_graph()
    nodes = _walk(g)
    out = {}
    d = nodes.get(N_DEDUP)
    if d is None:
        out["dedup"] = None
    else:
        dd = d["data"]
        out["dedup"] = {
            "title": dd.get("title"),
            "language": dd.get("code_language"),
            "outputs": dd.get("outputs"),
            "variables": dd.get("variables"),
            "code_head": (dd.get("code") or "").strip().splitlines()[0:2],
            "is_stub": "arg1 + arg2" in (dd.get("code") or ""),
        }
    f = nodes.get(N_FINAL)
    out["final_context"] = (f or {}).get("data", {}).get("context")
    out["edges"] = [(e.get("source"), e.get("target"))
                    for e in g["edges"]
                    if N_ITER in (e.get("source"), e.get("target"))
                    or N_DEDUP in (e.get("source"), e.get("target"))]
    return out


# ---------------------------------------------------------------- 命令
def cmd_check():
    info = describe()
    print("应用：%s（%s）" % (APP_NAME, APP_ID))
    print("草稿节点数：%d" % len(base.get_graph()["nodes"]))
    print("\n【去重节点 %s】" % N_DEDUP)
    if info["dedup"] is None:
        print("  ❌ 不存在")
    else:
        for k, v in info["dedup"].items():
            print("  %-9s %s" % (k + ":", v))
    print("\n【汇总作答 context】", json.dumps(info["final_context"], ensure_ascii=False))
    print("\n【相关连线】")
    for a, b in info["edges"]:
        print("  %s -> %s" % (a, b))
    yes = (info["dedup"] and not info["dedup"]["is_stub"]
           and info["final_context"].get("variable_selector", [None])[0] == N_DEDUP)
    print("\n状态：%s" % ("✅ 去重已接线生效" if yes else "⚠️ 去重未生效（空壳 / 未接线）"))


def cmd_selftest():
    """用合成数据验证去重代码的正确性（不碰数据库）。"""
    ns = {}
    exec(compile(DEDUP_CODE, "<dedup>", "exec"), ns)
    f = ns["main"]

    def item(ds, doc, seg, content, score):
        return {"content": content, "title": "t", "files": None,
                "metadata": {"_source": "knowledge", "dataset_id": ds,
                             "document_id": doc, "segment_id": seg,
                             "score": score}}

    A = item("d1", "doc1", "s1", "甲段内容", 0.71)
    A_hi = item("d1", "doc1", "s1", "甲段内容", 0.93)      # 同段更高分
    A_dup = item("d1", "doc1", "s1", "甲段内容", 0.55)
    B = item("d1", "doc1", "s2", "乙段内容", 0.80)
    C_same_text = item("d2", "doc9", "s9", "乙段内容", 0.90)  # 正文一样 -> 视为重复
    D_noseg = item("d1", "doc3", "", "丙段内容", 0.60)
    same_content_A = item("d1", "doc1", "s1", "甲段内容", 0.40)

    src = [A, A_dup, B, C_same_text, D_noseg, same_content_A, A_hi,
           {"content": "   ", "metadata": {}}, "字符串垃圾", None]

    r = f(src)
    got = r.get("result")
    ok = True

    def chk(cond, msg):
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + msg)
        if not cond:
            ok = False

    print("输入 %d 条（含重复/脏数据），输出 %d 条：" % (len(src), len(got)))
    for x in got:
        print("    - seg=%-4s score=%.2f  %s" % (
            x["metadata"].get("segment_id") or "-", x["metadata"]["score"],
            x["content"]))
    chk(len(got) == 3, "去重后剩 3 条（甲段+乙段+丙段）")
    chk(got[0]["metadata"]["score"] == 0.93, "甲段保留的是最高分 0.93")
    chk(sum(1 for x in got if x["content"] == "乙段内容") == 1, "正文相同的乙段只留 1 条")
    chk(all(isinstance(x.get("content"), str) and x["content"].strip() for x in got),
        "脏数据（空正文/字符串/None）被丢掉")
    chk(f(None) == {"result": []}, "输入 None 返回空数组")
    chk(f([]) == {"result": []}, "输入空数组返回空数组")
    one = {"content": "x", "metadata": {"dataset_id": "d", "segment_id": "s", "score": 1}}
    chk(len(f(one)["result"]) == 1, "输入单个 object（非数组）也能处理")
    chk(all(set(x.keys()) >= {"content", "metadata"} for x in got),
        "保留原条目全部字段（content + metadata 完整，引用信息不丢）")
    print("\n自检：%s" % ("✅ 全部通过" if ok else "❌ 有失败"))
    return ok


def cmd_apply(do_publish=False):
    g = base.get_graph()
    nodes = _walk(g)

    if N_DEDUP not in nodes:
        raise SystemExit("草稿里找不到「去重」节点 %s" % N_DEDUP)

    name, path = base.snapshot("before_dedup")
    print("[1/6] 已备份草稿：%s" % path)

    d = nodes[N_DEDUP]["data"]
    old_head = (d.get("code") or "").strip().splitlines()[0:1]
    d["code"] = DEDUP_CODE
    d["code_language"] = "python3"
    d["outputs"] = {"result": {"type": "array[object]", "children": None}}
    d["variables"] = [{"variable": "items", "value_selector": [N_ITER, "output"]}]
    d["title"] = "去重"
    d["desc"] = "按分段去掉重复的检索结果，只保留分数最高的一份"
    d.setdefault("type", "code")
    print("[2/6] 已写入去重代码；原代码首行：%s" % (old_head or ["(空)"]))

    f = nodes[N_FINAL]["data"]
    old_sel = json.dumps(f.get("context", {}).get("variable_selector"), ensure_ascii=False)
    f.setdefault("context", {})["enabled"] = True
    f["context"]["variable_selector"] = [N_DEDUP, "result"]
    print("[3/6] 汇总作答 context：%s -> %s" % (old_sel, json.dumps([N_DEDUP, "result"])))

    bad = base.validate_refs(g)
    if bad:
        raise SystemExit("图校验失败，悬空引用：%s" % bad)
    bad2 = base.validate_edges(g)
    if bad2:
        raise SystemExit("连线校验失败：%s" % bad2)
    print("[4/6] 图校验通过（无悬空引用 / 连线正常）")

    # 写库前先本地跑一遍代码，避免把坏代码写进去
    ns = {}
    exec(compile(DEDUP_CODE, "<dedup>", "exec"), ns)
    assert len(ns["main"]([{"content": "a", "metadata": {"segment_id": "s"}}])["result"]) == 1
    print("[5/6] 去重代码本地自测通过")

    base.write_draft(g)
    print("     已写回 Dify 草稿")

    if do_publish:
        wid, vnum, ver, wf_type = base.publish(
            "dedup-citations", "去重节点接线：按分段去重检索结果，去掉重复的引用")
        print("[6/6] 已发布 version#%d（%s，type=%s）" % (vnum, wid[:8], wf_type))
    else:
        print("[6/6] 未发布（加 --publish 才生效到运行时）")
    print("\n回退：python wf50_patch4.py rollback %s" % name)
    return name


def cmd_rollback(name):
    path = os.path.join(BACKUP_DIR, name + ".json")
    if not os.path.exists(path):
        raise SystemExit("找不到快照 %s" % path)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    base.snapshot("before_rollback")
    base.write_draft(data["graph"])
    print("已回退草稿到 %s（回退前的草稿另存了一份）" % name)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd = args[0]
    if cmd == "check":
        cmd_check()
    elif cmd == "selftest":
        sys.exit(0 if cmd_selftest() else 1)
    elif cmd == "apply":
        cmd_apply("--publish" in args)
    elif cmd == "rollback":
        if len(args) < 2:
            raise SystemExit("用法: python wf50_patch4.py rollback <快照名>")
        cmd_rollback(args[1])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
