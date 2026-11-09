#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""「问答5.0」第二阶段精简：去掉「来源判断」，有附件即入库。  22 -> 21 节点

用户要求：**不需要判断来源，只要有附件就进入知识库分支**。

改动：
  1. 删掉 if-else「判断文件来源」（原来按 source=var/false 分流到两个文档提取器）
  2. 「取文件名/判断来源」改名「读取文件信息」，**前置**到入口：开始 -> 读取文件信息 -> 分支
     并去掉 source 输出（没人用了），只保留 filename / filename_esc / has_file
  3. 入口分支「条件分支 5」的判据由 `query contains 知识库` 改成 `has_file is true`
     —— 有附件（对话附件或开始节点「医学知识文件」任一）走入库，没有附件走问答检索
  4. 两个文档提取器**并行**跑，都直连「校验正文」，由它取最长的那份当正文
     （空的那个会立即返回空数组，没有额外开销）
  5. 「校验正文」的 source 参数去掉，reason 文案精简（无附件已走不到这里）

为什么不能让 if-else 直接判 `sys.files not empty`：
  graphon 的 ConditionProcessor 在 `variable_pool.get(selector)` 为 None 时直接
  `raise ValueError("Variable ... not found")`。开始节点的文件变量未填时不一定在
  变量池里，用它做条件有炸节点的风险；先用代码节点算成布尔最稳。

命令（全部可回退）：
  python wf50_patch2.py check
  python wf50_patch2.py apply [--publish]
  python wf50_patch2.py rollback <快照名>
"""
import datetime as dt
import json
import os
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(HERE, "backup")

APP_ID = "1d7f324b-9865-4412-9e7f-53aa82decc24"
APP_NAME = "问答5.0"
PG_CONTAINER = "docker-db_postgres-1"
DB_NAME = "dify"
DB_USER = "postgres"

N_START = "1788759963315"        # 开始
N_QBRANCH = "1791687247358"      # 条件分支 5  ->  是否有附件
N_FILESRC = "node_code_filesrc"  # 取文件名/判断来源 -> 读取文件信息（前置）
N_IF_FILESRC = "node_if_filesrc"  # 判断文件来源（删除）
N_EXT_SYS = "node_ext_sys"       # 文档提取器(对话附件)
N_EXT_VAR = "node_ext_var"       # 文档提取器(文件变量)
N_CHECK = "node_code_check_text"  # 校验正文
N_IF_TEXT_OK = "node_if_text_ok"  # 正文是否有效（顺手修掉被编辑器改坏的布尔条件）
N_ANS_BADFILE = "node_answer_badfile"  # 提示-未能提取正文
N_LLM_SIMILAR = "node_llm_similar"    # 生成五个相似问

DELETE_NODES = [N_IF_FILESRC]
TARGET_NODE_COUNT = 21

# ---- 「读取文件信息」新代码（去掉 source）----------------------------------
NEW_FILESRC_CODE = '''import json


def _name_of(f):
    if f is None:
        return None
    if isinstance(f, str):
        return f or None
    if isinstance(f, dict):
        return f.get("name") or f.get("filename") or None
    return getattr(f, "name", None) or getattr(f, "filename", None)


def _as_list(x):
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


def main(files, file_var) -> dict:
    # 对话附件优先；没有对话附件就用开始节点的「医学知识文件」。不区分来源，
    # 只要任一路有文件就算「有附件」，直接进入知识库入库分支。
    all_files = _as_list(files) + _as_list(file_var)
    name = _name_of(all_files[0]) if all_files else None
    name = str(name) if name else "医学知识文件"
    return {
        "filename": name,
        "filename_esc": json.dumps(name, ensure_ascii=False)[1:-1],
        # 故意用「非空字符串」而不是布尔：Dify 编辑器的 if-else 会把布尔条件值
        # 改写成字符串（false -> "true"），而 `not empty` 是一元判断、没有期望值，
        # 不会被编辑器改坏。
        "file_flag": "1" if all_files else "",
    }
'''

# ---- 「校验正文」新代码（去掉 source 参数，新增 verdict 字符串）------------
NEW_CHECK_CODE = '''def _flatten(x):
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if isinstance(x, (list, tuple)):
        return "\\n".join(_flatten(i) for i in x)
    if isinstance(x, dict):
        return str(x.get("text") or x.get("content") or "")
    return str(x)


def main(doc_text, var_text) -> dict:
    # 两个提取器并行跑，只有一个会拿到文件，取最长的那份当正文
    parts = [_flatten(doc_text), _flatten(var_text)]
    body = max(parts, key=lambda s: len(s.strip())).strip()
    n = len(body)
    ok = n >= 10  # 少于 10 个字符基本就是空的 / 提取失败
    if ok:
        reason = "提取正常"
    else:
        reason = ("文件已收到但没提取到正文（可能原因：扫描件/图片型 PDF 无文字层；"
                  "DOCX 结构损坏导致解析失败；文档本身为空）")
    return {
        "ok": ok,
        "reason": reason,
        "doc_chars": n,
        "text": body,
        # 给 if-else 用的一元判断信号（空 = 正常，非空 = 有问题）
        "verdict": "" if ok else "bad",
    }
'''

NEW_BADFILE_ANSWER = (
    "⚠️ 这份文件没有可用于生成问答对的正文，已跳过入库。\n\n"
    "- 原因：{{#node_code_check_text.reason#}}\n"
    "- 已提取到的正文字符数：{{#node_code_check_text.doc_chars#}}\n\n"
    "常见原因：\n"
    "- 文件是扫描件、图片型 PDF，没有文字层；\n"
    "- DOCX 内部结构损坏（例如内嵌图片 CRC 校验失败）；\n"
    "- 文档本身就是空的。"
)


# ------------------------------------------------------------------ psql
def _psql(sql="", *, tuples_only=False, stdin_text=None):
    cmd = ["docker", "exec", "-i", PG_CONTAINER, "psql", "-U", DB_USER,
           "-d", DB_NAME, "-q", "-v", "ON_ERROR_STOP=1"]
    if tuples_only:
        cmd += ["-t", "-A"]
    cmd += ["-f", "-"]
    p = subprocess.run(cmd, input=(stdin_text if stdin_text is not None else sql),
                       capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise RuntimeError("psql 失败:\n" + (p.stderr or p.stdout))
    return p.stdout


def _dollar(js, tag="difywf"):
    if ("$%s$" % tag) in js:
        raise ValueError("内容里含有 dollar 引用标记，无法安全写入")
    return "$%s$%s$%s$" % (tag, js, tag)


def _col(expr, version="draft"):
    return _psql("SELECT %s FROM workflows WHERE app_id='%s' AND version='%s';"
                 % (expr, APP_ID, version), tuples_only=True).rstrip("\n")


def get_graph():
    return json.loads(_col("graph"))


def write_draft(graph):
    sql = ("UPDATE workflows SET graph = %s, updated_at = now() "
           "WHERE app_id='%s' AND version='draft';\n"
           % (_dollar(json.dumps(graph, ensure_ascii=False), "difyg"), APP_ID))
    _psql("", stdin_text=sql)


def snapshot(label):
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = "".join(c for c in label if c.isalnum() or c in "-_") or "snap"
    name = "wf50b_%s_%s" % (ts, safe)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    path = os.path.join(BACKUP_DIR, name + ".json")
    feats = _col("features")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"name": name, "at": ts, "app_id": APP_ID, "app_name": APP_NAME,
                   "graph": json.loads(_col("graph")),
                   "features": json.loads(feats) if feats else {},
                   "workflow_id": _psql(
                       "SELECT workflow_id FROM apps WHERE id='%s';" % APP_ID,
                       tuples_only=True).strip()},
                  f, ensure_ascii=False, indent=1)
    return name, path


# ------------------------------------------------------------------ 图操作
def find_node(graph, node_id):
    for n in graph["nodes"]:
        if n["id"] == node_id:
            return n
    return None


def del_nodes(graph, ids):
    before = len(graph["nodes"])
    graph["nodes"] = [n for n in graph["nodes"] if n["id"] not in ids]
    graph["edges"] = [e for e in graph["edges"]
                      if e.get("source") not in ids and e.get("target") not in ids]
    return before - len(graph["nodes"])


def del_edge(graph, source, target):
    n = len(graph["edges"])
    graph["edges"] = [e for e in graph["edges"]
                      if not (e.get("source") == source and e.get("target") == target)]
    return n - len(graph["edges"])


def set_edge(graph, source, target, source_handle, target_handle="target",
             source_type="custom", target_type="custom"):
    for e in graph["edges"]:
        if e.get("source") == source and e.get("target") == target:
            e["sourceHandle"] = source_handle
            e["targetHandle"] = target_handle
            return "updated"
    graph["edges"].append({
        "id": "e-%s-%s" % (source, target),
        "source": source, "target": target,
        "sourceHandle": source_handle, "targetHandle": target_handle,
        "type": "custom", "zIndex": 0,
        "data": {"sourceType": source_type, "targetType": target_type},
    })
    return "added"


def move(graph, node_id, x, y):
    n = find_node(graph, node_id)
    if not n:
        return
    n["position"] = {"x": x, "y": y}
    n["positionAbsolute"] = {"x": x, "y": y}


# ------------------------------------------------------------------ 改动
def rewrite_filesrc(graph):
    n = find_node(graph, N_FILESRC)
    if not n:
        raise SystemExit("找不到节点 %s（取文件名/判断来源）" % N_FILESRC)
    n["data"]["title"] = "读取文件信息"
    n["data"]["desc"] = "取文件名（给知识库命名）；file_flag 非空 = 对话附件或开始节点文件变量里有文件"
    n["data"]["code"] = NEW_FILESRC_CODE
    n["data"]["outputs"] = {
        "filename": {"children": None, "type": "string"},
        "filename_esc": {"children": None, "type": "string"},
        "file_flag": {"children": None, "type": "string"},
    }
    return n


def rewrite_qbranch(graph):
    """入口分支：有附件 -> 入库；没附件 -> 问答检索。

    条件用一元运算符 `not empty`：没有期望值可被编辑器改写，
    天然避开「布尔条件值被 Dify 编辑器改写成字符串」那个坑。
    """
    n = find_node(graph, N_QBRANCH)
    if not n:
        raise SystemExit("找不到节点 %s（条件分支 5）" % N_QBRANCH)
    n["data"]["title"] = "是否有附件"
    n["data"]["desc"] = ("命中 true = 有附件（对话附件或开始节点「医学知识文件」），"
                         "进入知识库入库；false = 没有附件，走问答检索")
    n["data"]["cases"] = [{
        "case_id": "true",
        "logical_operator": "and",
        "conditions": [{
            "id": str(uuid.uuid4()),
            "comparison_operator": "not empty",
            "value": "",
            "varType": "string",
            "variable_selector": [N_FILESRC, "file_flag"],
        }],
    }]
    n["data"]["logical_operator"] = "and"


def fix_text_ok(graph):
    """修掉「正文是否有效」被编辑器改坏的布尔条件（false -> 字符串 "true"）。

    改成对 `校验正文.verdict` 做一元 `not empty`：verdict 非空表示正文有问题。
    """
    n = find_node(graph, N_IF_TEXT_OK)
    if not n:
        raise SystemExit("找不到节点 %s（正文是否有效）" % N_IF_TEXT_OK)
    n["data"]["desc"] = "命中 bad = 没提取到正文，给提示；否则正常生成问答对并入库"
    n["data"]["cases"] = [{
        "case_id": "bad",
        "logical_operator": "and",
        "conditions": [{
            "id": str(uuid.uuid4()),
            "comparison_operator": "not empty",
            "value": "",
            "varType": "string",
            "variable_selector": [N_CHECK, "verdict"],
        }],
    }]
    n["data"]["logical_operator"] = "and"


def rewrite_check(graph):
    n = find_node(graph, N_CHECK)
    if not n:
        raise SystemExit("找不到节点 %s（校验正文）" % N_CHECK)
    n["data"]["code"] = NEW_CHECK_CODE
    n["data"]["desc"] = "取两份提取结果里最长的一份当正文，并判断是否有效"
    n["data"]["outputs"] = {
        "ok": {"children": None, "type": "boolean"},
        "reason": {"children": None, "type": "string"},
        "doc_chars": {"children": None, "type": "number"},
        "text": {"children": None, "type": "string"},
        "verdict": {"children": None, "type": "string"},
    }
    n["data"]["variables"] = [
        {"variable": "doc_text", "value_selector": [N_EXT_SYS, "text"], "value_type": "array[string]"},
        {"variable": "var_text", "value_selector": [N_EXT_VAR, "text"], "value_type": "array[string]"},
    ]


def rewire(graph):
    """入口链路 + 两个提取器并行接「校验正文」。"""
    # 入口：开始 -> 读取文件信息 -> 是否有附件
    del_edge(graph, N_START, N_QBRANCH)
    del_edge(graph, N_QBRANCH, N_FILESRC)
    set_edge(graph, N_START, N_FILESRC, "source",
             source_type="start", target_type="code")
    set_edge(graph, N_FILESRC, N_QBRANCH, "source",
             source_type="code", target_type="if-else")
    # true 分支扇出到两个提取器；两个提取器都直连「校验正文」
    del_edge(graph, N_IF_FILESRC, N_EXT_VAR)
    del_edge(graph, N_IF_FILESRC, N_EXT_SYS)
    set_edge(graph, N_QBRANCH, N_EXT_SYS, "true",
             source_type="if-else", target_type="document-extractor")
    set_edge(graph, N_QBRANCH, N_EXT_VAR, "true",
             source_type="if-else", target_type="document-extractor")
    set_edge(graph, N_EXT_SYS, N_CHECK, "source",
             source_type="document-extractor", target_type="code")
    set_edge(graph, N_EXT_VAR, N_CHECK, "source",
             source_type="document-extractor", target_type="code")


def relayout(graph):
    """让前段链路在画布上连成一条直线，便于阅读。"""
    move(graph, N_START, 80, 320)
    move(graph, N_FILESRC, 380, 320)
    move(graph, N_QBRANCH, 680, 320)
    move(graph, N_EXT_SYS, 980, 60)
    move(graph, N_EXT_VAR, 980, 400)
    move(graph, N_CHECK, 1280, 230)


def fix_badfile(graph):
    n = find_node(graph, N_ANS_BADFILE)
    if not n:
        raise SystemExit("找不到节点 %s" % N_ANS_BADFILE)
    n["data"]["title"] = "提示-文件无正文"
    n["data"]["desc"] = "有附件但没提取到正文（无附件已走问答分支，不会到这里）"
    n["data"]["answer"] = NEW_BADFILE_ANSWER


# ------------------------------------------------------------------ 校验
def validate_refs(graph):
    ids = {n["id"] for n in graph["nodes"]}
    bad = []
    for n in graph["nodes"]:
        raw = json.dumps(n["data"], ensure_ascii=False)
        i = 0
        while True:
            a = raw.find("{{#", i)
            if a < 0:
                break
            b = raw.find("#}}", a)
            if b < 0:
                break
            ref = raw[a + 3:b]
            nid = ref.split(".")[0]
            if nid and nid not in ids and nid not in ("sys", "conversation", "env", "context"):
                bad.append((n["id"], n["data"].get("title"), ref))
            i = b + 3
    return bad


def validate_edges(graph):
    ids = {n["id"] for n in graph["nodes"]}
    bad = []
    for e in graph["edges"]:
        if e.get("source") not in ids:
            bad.append("边的 source 不存在: %s" % e.get("source"))
        if e.get("target") not in ids:
            bad.append("边的 target 不存在: %s" % e.get("target"))
    has_in = {e["target"] for e in graph["edges"]}
    for n in graph["nodes"]:
        if n["id"] in (N_START, "node_iteration_start"):
            continue
        if n["data"].get("type") == "iteration-start":
            continue
        if n["id"] not in has_in:
            bad.append("节点没有入边: %s (%s)" % (n["id"], n["data"].get("title")))
    # 入口链路检查
    pairs = {(e["source"], e["target"], e.get("sourceHandle")) for e in graph["edges"]}
    for want in ((N_START, N_FILESRC, "source"), (N_FILESRC, N_QBRANCH, "source"),
                 (N_QBRANCH, N_EXT_SYS, "true"), (N_QBRANCH, N_EXT_VAR, "true"),
                 (N_QBRANCH, N_LLM_SIMILAR, "false"),
                 (N_EXT_SYS, N_CHECK, "source"), (N_EXT_VAR, N_CHECK, "source")):
        if want not in pairs:
            bad.append("缺少连线 %s --%s--> %s" % (want[0], want[2], want[1]))
    return bad


def verify_spec(graph):
    issues = []
    if len(graph["nodes"]) != TARGET_NODE_COUNT:
        issues.append("节点数=%d（目标 %d）" % (len(graph["nodes"]), TARGET_NODE_COUNT))
    for nid in DELETE_NODES:
        if find_node(graph, nid):
            issues.append("应删除的节点还在: %s" % nid)

    n = find_node(graph, N_FILESRC)
    if not n:
        issues.append("缺少 %s" % N_FILESRC)
    else:
        code = n["data"].get("code") or ""
        outs = n["data"].get("outputs") or {}
        if "file_flag" not in outs:
            issues.append("读取文件信息: 缺少 file_flag 输出")
        for gone in ("source", "has_file"):
            if gone in outs:
                issues.append("读取文件信息: 仍在输出 %s" % gone)
        if "_as_list(files) + _as_list(file_var)" not in code:
            issues.append("读取文件信息: 没有合并两路文件")

    n = find_node(graph, N_QBRANCH)
    if not n:
        issues.append("缺少 %s" % N_QBRANCH)
    else:
        cases = n["data"].get("cases") or []
        if len(cases) != 1:
            issues.append("是否有附件: cases 数量=%d（应为 1）" % len(cases))
        else:
            conds = cases[0].get("conditions") or []
            if cases[0].get("case_id") != "true":
                issues.append("是否有附件: case_id=%r（应为 true）" % cases[0].get("case_id"))
            if len(conds) != 1:
                issues.append("是否有附件: 条件数=%d（应为 1）" % len(conds))
            else:
                c = conds[0]
                if c.get("variable_selector") != [N_FILESRC, "file_flag"]:
                    issues.append("是否有附件: 变量=%s（应为 读取文件信息.file_flag）" % c.get("variable_selector"))
                if c.get("comparison_operator") != "not empty":
                    issues.append("是否有附件: 运算符=%r（应为 not empty）" % c.get("comparison_operator"))

    n = find_node(graph, N_IF_TEXT_OK)
    if not n:
        issues.append("缺少 %s" % N_IF_TEXT_OK)
    else:
        cases = n["data"].get("cases") or []
        conds = (cases[0].get("conditions") or []) if cases else []
        if not conds:
            issues.append("正文是否有效: 没有条件")
        else:
            c = conds[0]
            if c.get("variable_selector") != [N_CHECK, "verdict"]:
                issues.append("正文是否有效: 变量=%s（应为 校验正文.verdict）" % c.get("variable_selector"))
            if c.get("comparison_operator") != "not empty":
                issues.append("正文是否有效: 运算符=%r（应为 not empty，避免布尔值被编辑器改写）"
                              % c.get("comparison_operator"))

    n = find_node(graph, N_CHECK)
    if not n:
        issues.append("缺少 %s" % N_CHECK)
    else:
        v = json.dumps(n["data"].get("variables"), ensure_ascii=False)
        if "source" in v or "has_file" in v or "file_flag" in v:
            issues.append("校验正文: 仍引用了不该有的变量")
        if "verdict" not in (n["data"].get("outputs") or {}):
            issues.append("校验正文: 缺少 verdict 输出")
        for want in (N_EXT_SYS, N_EXT_VAR):
            if ('["%s", "text"]' % want) not in v.replace("'", '"'):
                issues.append("校验正文: 没有接入 %s.text" % want)

    bad = validate_edges(graph)
    issues += bad
    return (not issues), issues


# ------------------------------------------------------------------ 发布
def publish(marked_name="", marked_comment=""):
    version = str(dt.datetime.now(dt.timezone.utc).replace(tzinfo=None))
    out = _psql(
        "INSERT INTO workflow_version_counters (app_id, last_version_number) "
        "VALUES ('%s', 1) ON CONFLICT (app_id) DO UPDATE SET "
        "last_version_number = workflow_version_counters.last_version_number + 1 "
        "RETURNING last_version_number;" % APP_ID, tuples_only=True)
    vnum = int(out.strip().splitlines()[-1].strip())
    wf_type = _col("type")
    wid = _psql(
        "INSERT INTO workflows (tenant_id, app_id, type, version, version_number, "
        "graph, features, created_by, updated_by, marked_name, marked_comment, kind, "
        "updated_at) "
        "SELECT tenant_id, app_id, type, %s, %d, graph, features, created_by, "
        "created_by, %s, %s, kind, now() FROM workflows "
        "WHERE app_id='%s' AND version='draft' RETURNING id;"
        % (_dollar(version, "difyv"), vnum, _dollar(marked_name, "difymn"),
           _dollar(marked_comment, "difymc"), APP_ID),
        tuples_only=True)
    new_id = wid.strip().splitlines()[-1].strip()
    _psql("UPDATE apps SET workflow_id='%s', updated_at=now() WHERE id='%s';"
          % (new_id, APP_ID))
    return new_id, vnum, version, wf_type


# ------------------------------------------------------------------ 命令
def cmd_check():
    g = get_graph()
    print("应用：%s（%s）" % (APP_NAME, APP_ID))
    print("节点数：%d   连线数：%d" % (len(g["nodes"]), len(g["edges"])))
    n = find_node(g, N_QBRANCH)
    if n:
        print("入口分支 %s：" % N_QBRANCH)
        for c in n["data"].get("cases") or []:
            print("   case %-6s %s" % (c.get("case_id"), c.get("conditions")))
    print("入口连线：")
    for e in g["edges"]:
        if e.get("source") in (N_START, N_FILESRC, N_QBRANCH, N_EXT_SYS, N_EXT_VAR, N_CHECK):
            print("   %-24s --%-6s--> %-24s" % (e.get("source"), e.get("sourceHandle"), e.get("target")))
    print("-" * 78)
    print("悬空变量引用：%d 处" % len(validate_refs(g)))
    for it in validate_refs(g):
        print("   ", it)
    ok, issues = verify_spec(g)
    print("规格自检（21 节点 / 有附件即入库）：%s" % ("✅ 通过" if ok else "❌ 未达标"))
    for it in issues:
        print("   -", it)


def cmd_verify():
    g = get_graph()
    ok, issues = verify_spec(g)
    print("草稿规格自检：%s" % ("✅ 通过" if ok else "❌ 不通过"))
    for it in issues:
        print("   -", it)
    raise SystemExit(0 if ok else 1)


def cmd_apply(do_publish=False):
    g = get_graph()
    name, path = snapshot("before_nosrc")
    before_n = len(g["nodes"])
    print("[1/6] 已备份当前草稿 -> %s（%d 节点）" % (path, before_n))

    rewrite_filesrc(g)
    rewrite_qbranch(g)
    rewrite_check(g)
    fix_text_ok(g)
    fix_badfile(g)
    rewire(g)
    gone = del_nodes(g, DELETE_NODES)
    relayout(g)
    print("[2/6] 已去掉「来源判断」：%d 节点 -> %d（删除 %d）；"
          "两处 if-else 条件改为一元 `not empty`" % (before_n, len(g["nodes"]), gone))

    bad = validate_refs(g)
    if bad:
        raise SystemExit("图校验失败，悬空引用：%s" % bad)
    ok, issues = verify_spec(g)
    print("[3/6] 规格自检 %s" % ("✅ 通过" if ok else "❌ 不通过"))
    for it in issues:
        print("      -", it)
    if not ok:
        raise SystemExit("规格自检不通过，未写库。")

    write_draft(g)
    print("[4/6] 已写回 Dify 草稿")

    if do_publish:
        wid, vnum, ver, wf_type = publish("no-source-judge", "去掉来源判断，有附件即入库")
        print("[5/6] 已发布 version#%d（%s，type=%s）" % (vnum, wid[:8], wf_type))
    else:
        print("[5/6] 未发布（加 --publish 才生效到运行时）")
    print("[6/6] 回退命令：python wf50_patch2.py rollback %s" % name)
    return name


def cmd_rollback(name):
    path = os.path.join(BACKUP_DIR, name + ".json")
    if not os.path.exists(path):
        raise SystemExit("找不到快照 %s" % path)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    snapshot("before_rollback")
    write_draft(data["graph"])
    print("已回退草稿到 %s（回退前的草稿另存了一份）" % name)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd = args[0]
    if cmd == "check":
        cmd_check()
    elif cmd == "verify":
        cmd_verify()
    elif cmd == "apply":
        cmd_apply("--publish" in args)
    elif cmd == "rollback":
        if len(args) < 2:
            raise SystemExit("用法: python wf50_patch2.py rollback <快照名>")
        cmd_rollback(args[1])
    elif cmd == "publish":
        wid, vnum, ver, wf_type = publish("manual", "")
        print("已发布 version#%d（%s）" % (vnum, wid[:8]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
