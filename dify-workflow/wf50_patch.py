#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""针对 Dify 应用「问答5.0」(app_id 1d7f324b-...) 的**节点精简**工具：28 -> 22 个节点。

精简内容（5 处，逐一可回退）：

  A. 「判断文件来源」+「是否有对话附件」两个 if-else  ->  合并成 1 个 if-else
     （var 走文件变量提取器，else 走对话附件提取器）
  B. 去掉「正文汇总」variable-aggregator，把「取最长正文」的聚合逻辑
     并入「校验正文」代码节点（该节点改为输出 text / ok / reason / doc_chars）
  C. 去掉「查找同名知识库」+「知识库是否已存在」两个节点：
     「新建知识库」改为**总是调用**并配置 error_strategy=default-value
     （重名时 Dify 返回 409 -> 落默认值），由「确定知识库ID」先按文件名命中
     本地知识库列表、再退回新建返回体里的 id。语义与原两节点完全一致。
  D. 去掉循环内的「逐问作答」LLM：循环直接输出内层「知识检索」的 result，
     「去重汇总作答」改用 LLM 节点的 context 通道接住检索结果
     （graphon 的 _parse_context_item 会只取 content，不掺 JSON 噪音）。
  E. 去掉「提示-未上传文件」，并入「提示-未能提取正文」：没有文件时
     提取器返回空数组，「校验正文」按 source=='none' 给出「没有收到文件内容」的提示。

命令（全部可回退）：
  python wf50_patch.py check                 # 只读查看现状 + 自检
  python wf50_patch.py backup                # 备份草稿
  python wf50_patch.py apply [--publish]     # 改草稿（可选发布）
  python wf50_patch.py rollback <快照名>      # 用备份还原草稿
  python wf50_patch.py publish               # 把当前草稿发布成新版本
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

# ---- 节点 id --------------------------------------------------------------
N_START = "1788759963315"        # 开始
N_QUERY_BRANCH = "1791687247358"  # 条件分支 5（问题里是否含「知识库」）
N_FILESRC = "node_code_filesrc"      # 取文件名/判断来源
N_IF_FILESRC = "node_if_filesrc"     # 判断文件来源（<- 合并 A）
N_IF_HASFILE = "node_if_hasfile"     # 是否有对话附件（删除）
N_EXT_VAR = "node_ext_var"           # 文档提取器(文件变量)
N_EXT_SYS = "node_ext_sys"           # 文档提取器(对话附件)
N_AGG_TEXT = "node_agg_text"         # 正文汇总（删除）
N_CHECK = "node_code_check_text"     # 校验正文（<- 合并 B）
N_IF_TEXT_OK = "node_if_text_ok"     # 正文是否有效
N_ANS_BADFILE = "node_answer_badfile"  # 提示-未能提取正文（<- 合并 E）
N_ANS_HINT = "node_answer_hint"      # 提示-未上传文件（删除）
N_LLM_GEN_QA = "node_llm_gen_qa"     # 生成问答对
N_HTTP_LIST = "node_http_list_kb"    # 查知识库列表
N_FIND_KB = "node_code_find_kb"      # 查找同名知识库（删除）
N_IF_KB = "node_if_kb"               # 知识库是否已存在（删除）
N_HTTP_CREATE = "node_http_create_kb"  # 新建知识库（<- 合并 C）
N_DECIDE = "node_code_decide_id"     # 确定知识库ID（<- 合并 C）
N_HTTP_ADD = "node_http_add_doc"     # 写入问答对txt
N_ANS_FILE = "node_answer_file"      # 文件分支-回复
N_LLM_SIMILAR = "node_llm_similar"   # 生成五个相似问
N_PARSE_Q = "node_code_parse_q"      # 解析相似问
N_ITER = "node_iteration"            # 循环检索知识库回答
N_ITER_START = "node_iteration_start"
N_RETRIEVAL = "1788759983275"        # 知识检索
N_LLM_QA_ONE = "llm"                 # 逐问作答（删除，<- 合并 D）
N_LLM_FINAL = "node_llm_final"       # 去重汇总作答（<- 合并 D）
N_ANSWER = "answer"                  # 直接回复

DELETE_NODES = [N_IF_HASFILE, N_ANS_HINT, N_AGG_TEXT, N_FIND_KB, N_IF_KB, N_LLM_QA_ONE]
TARGET_NODE_COUNT = 22

# ---- 合并 B：「校验正文」新代码 -------------------------------------------
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


def main(doc_text, var_text, source) -> dict:
    # 两个提取器只有一个会跑到（if-else 分流），取最长的那份当正文
    parts = [_flatten(doc_text), _flatten(var_text)]
    body = max(parts, key=lambda s: len(s.strip())).strip()
    n = len(body)
    ok = n >= 10  # 少于 10 个字符基本就是空的 / 提取失败
    if source == "none":
        reason = ("没有收到文件内容：请在对话框的附件按钮上传文件，"
                  "或在开始节点的「医学知识文件」里选择文件后重试。")
    elif ok:
        reason = "提取正常"
    else:
        reason = ("文件已收到但没提取到正文（可能原因：扫描件/图片型 PDF 无文字层；"
                  "DOCX 结构损坏导致解析失败；文档本身为空）")
    return {"ok": ok, "reason": reason, "doc_chars": n, "text": body}
'''

# ---- 合并 C：「确定知识库ID」新代码 ---------------------------------------
NEW_DECIDE_CODE = '''import json


def _pick_json_lines(text: str) -> list:
    """把模型输出规整成「一行一个 JSON 对象」：
    1) 统一换行、去掉 markdown 代码围栏
    2) 用 raw_decode 逐个抽出 JSON 对象并重新序列化（容忍被折行/美化过的输出）
    3) 一个对象都抽不到时，退回「按行保留非空行」
    """
    s = (text or "").replace("\\r\\n", "\\n").replace("\\r", "\\n")
    s = "\\n".join(ln for ln in s.split("\\n") if not ln.strip().startswith("```"))

    objs, dec, i, n = [], json.JSONDecoder(), 0, len(s)
    while i < n:
        j = s.find("{", i)
        if j < 0:
            break
        try:
            obj, end = dec.raw_decode(s, j)
        except Exception:
            i = j + 1
            continue
        if isinstance(obj, dict):
            objs.append(json.dumps(obj, ensure_ascii=False))
        i = end
    if objs:
        return objs
    return [ln.strip() for ln in s.split("\\n") if ln.strip()]


def _find_in_list(list_body: str, name: str) -> str:
    """在「查知识库列表」的返回体里按名称精确匹配，命中则返回 dataset id。"""
    try:
        data = json.loads(list_body or "{}")
    except Exception:
        return ""
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return ""
    for it in items:
        if isinstance(it, dict) and str(it.get("name") or "").strip() == name:
            return str(it.get("id") or "")
    return ""


def main(list_body: str, create_body: str, filename: str, text: str) -> dict:
    name = (filename or "").strip()
    did = _find_in_list(list_body, name)      # 已存在 -> 复用（新建那步会拿到 409，落默认值）
    how = "复用已存在的知识库"
    if not did:                               # 不存在 -> 用「新建知识库」返回的 id
        try:
            data = json.loads(create_body or "{}")
        except Exception:
            data = {}
        if isinstance(data, dict):
            did = str(data.get("id") or "")
        how = "新建知识库" if did else "未取到知识库ID（请看节点运行日志）"
    lines = _pick_json_lines(text)            # 每行一个 JSON 语句 -> 一行 = 一个分段
    return {
        "dataset_id": did,
        "how": how,
        "text_esc": json.dumps("\\n".join(lines), ensure_ascii=False)[1:-1],
        "segments_expected": len(lines),
    }
'''

NEW_FINAL_SYS_PROMPT = (
    "你是医学知识助手。下面是对同一个用户问题（{{#sys.query#}}）用多种相似问法检索知识库后"
    "得到的若干条《知识库检索结果》。请：\n"
    "1. 去掉重复、冗余、与问题无关的内容；\n"
    "2. 合并成一个准确、完整、条理清晰的最终回答；\n"
    "3. 只依据这些内容作答，不得编造；若全部为空，说明「知识库中未找到相关内容」。\n\n"
    "《知识库检索结果》：\n{{#context#}}"
)

NEW_BADFILE_ANSWER = (
    "⚠️ 这次没有可用于生成问答对的正文，已跳过入库。\n\n"
    "- 原因：{{#node_code_check_text.reason#}}\n"
    "- 已提取到的正文字符数：{{#node_code_check_text.doc_chars#}}\n\n"
    "常见原因：\n"
    "- 没有上传文件（对话框附件 / 开始节点「医学知识文件」都没选）；\n"
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


# ------------------------------------------------------------------ 快照
def snapshot(label):
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = "".join(c for c in label if c.isalnum() or c in "-_") or "snap"
    name = "wf50_%s_%s" % (ts, safe)
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


def set_edge(graph, source, target, source_handle, target_handle="target"):
    """把 (source, target) 这条边改成指定的 sourceHandle；不存在则新建。"""
    for e in graph["edges"]:
        if e.get("source") == source and e.get("target") == target:
            e["sourceHandle"] = source_handle
            e["targetHandle"] = target_handle
            return "updated"
    graph["edges"].append({
        "id": "e-%s-%s" % (source, target),
        "source": source, "target": target,
        "sourceHandle": source_handle, "targetHandle": target_handle,
        "type": "custom", "zIndex": 0, "data": {"sourceType": "custom", "targetType": "custom"},
    })
    return "added"


def cond(var_selector, value, var_type=None):
    c = {"id": str(uuid.uuid4()), "comparison_operator": "is",
         "value": value, "variable_selector": var_selector}
    if var_type:
        c["varType"] = var_type
    return c


# ---- 各处改动 -------------------------------------------------------------
def merge_a_e(graph):
    """A：判断文件来源 + 是否有对话附件 -> 1 个 if-else；E：删掉「提示-未上传文件」。"""
    n = find_node(graph, N_IF_FILESRC)
    if not n:
        raise SystemExit("找不到节点 %s（判断文件来源）" % N_IF_FILESRC)
    n["data"]["title"] = "文件来源判断"
    n["data"]["desc"] = ("命中 var = 用开始节点「医学知识文件」；否则走对话附件。"
                         "没有文件时提取器返回空数组，由「校验正文」按 source=none 给提示。")
    n["data"]["cases"] = [{
        "case_id": "var",
        "conditions": [cond([N_FILESRC, "source"], "var", "string")],
        "logical_operator": "and",
    }]
    n["data"]["logical_operator"] = "and"
    set_edge(graph, N_IF_FILESRC, N_EXT_VAR, "var")
    set_edge(graph, N_IF_FILESRC, N_EXT_SYS, "false")


def merge_b(graph):
    """B：去掉「正文汇总」，聚合逻辑并入「校验正文」。"""
    n = find_node(graph, N_CHECK)
    if not n:
        raise SystemExit("找不到节点 %s（校验正文）" % N_CHECK)
    n["data"]["code"] = NEW_CHECK_CODE
    n["data"]["desc"] = "取两份提取结果里最长的一份当正文（原「正文汇总」聚合），并判断是否有效"
    n["data"]["outputs"] = {
        "ok": {"children": None, "type": "boolean"},
        "reason": {"children": None, "type": "string"},
        "doc_chars": {"children": None, "type": "number"},
        "text": {"children": None, "type": "string"},
    }
    n["data"]["variables"] = [
        {"variable": "doc_text", "value_selector": [N_EXT_SYS, "text"], "value_type": "array[string]"},
        {"variable": "var_text", "value_selector": [N_EXT_VAR, "text"], "value_type": "array[string]"},
        {"variable": "source", "value_selector": [N_FILESRC, "source"], "value_type": "string"},
    ]
    # 两个提取器直接接到「校验正文」
    set_edge(graph, N_EXT_SYS, N_CHECK, "source")
    set_edge(graph, N_EXT_VAR, N_CHECK, "source")


def merge_c(graph):
    """C：去掉「查找同名知识库」+「知识库是否已存在」，新建改为可容忍 409。"""
    create = find_node(graph, N_HTTP_CREATE)
    decide = find_node(graph, N_DECIDE)
    if not create or not decide:
        raise SystemExit("找不到 %s / %s" % (N_HTTP_CREATE, N_DECIDE))
    create["data"]["title"] = "确保知识库存在"
    create["data"]["desc"] = "总是调用；同名已存在时 Dify 返回 409，由 error_strategy 落默认值，随后按列表复用"
    create["data"]["error_strategy"] = "default-value"
    create["data"]["retry_config"] = {"max_retries": 0, "retry_enabled": False, "retry_interval": 0}
    create["data"]["default_value"] = [
        {"key": "body", "type": "string", "value": "{}"},
        {"key": "status_code", "type": "number", "value": 0},
    ]

    decide["data"]["code"] = NEW_DECIDE_CODE
    decide["data"]["desc"] = "先在知识库列表里按文件名命中（复用），否则用新建返回的 id；顺便转义正文"
    decide["data"]["outputs"] = {
        "dataset_id": {"children": None, "type": "string"},
        "how": {"children": None, "type": "string"},
        "segments_expected": {"children": None, "type": "number"},
        "text_esc": {"children": None, "type": "string"},
    }
    decide["data"]["variables"] = [
        {"variable": "list_body", "value_selector": [N_HTTP_LIST, "body"], "value_type": "string"},
        {"variable": "create_body", "value_selector": [N_HTTP_CREATE, "body"], "value_type": "string"},
        {"variable": "filename", "value_selector": [N_FILESRC, "filename"], "value_type": "string"},
        {"variable": "text", "value_selector": [N_LLM_GEN_QA, "text"], "value_type": "string"},
    ]
    # 查列表 -> 确保存在 -> 确定ID
    set_edge(graph, N_HTTP_LIST, N_HTTP_CREATE, "source")
    set_edge(graph, N_HTTP_CREATE, N_DECIDE, "source")


def merge_d(graph):
    """D：去掉循环内「逐问作答」，循环直接输出检索结果，「去重汇总作答」用 context 接住。"""
    it = find_node(graph, N_ITER)
    final = find_node(graph, N_LLM_FINAL)
    if not it or not final:
        raise SystemExit("找不到 %s / %s" % (N_ITER, N_LLM_FINAL))
    it["data"]["output_selector"] = [N_RETRIEVAL, "result"]
    it["data"]["output_type"] = "array[object]"
    it["data"]["desc"] = "用 5 个相似问各检索一次知识库，输出合并后的检索结果"

    final["data"]["context"] = {"enabled": True, "variable_selector": [N_ITER, "output"]}
    msgs = final["data"]["prompt_template"]
    for m in msgs:
        if m.get("role") == "system":
            m["text"] = NEW_FINAL_SYS_PROMPT
        elif m.get("role") == "user":
            m["text"] = "{{#sys.query#}}"


def fix_genqa_input(graph):
    """「生成问答对」的文档输入：正文汇总 -> 校验正文.text。"""
    n = find_node(graph, N_LLM_GEN_QA)
    if not n:
        raise SystemExit("找不到 %s（生成问答对）" % N_LLM_GEN_QA)
    for m in n["data"]["prompt_template"]:
        if m.get("role") == "user":
            m["text"] = "文档内容：\n{{#%s.text#}}" % N_CHECK


def fix_badfile_answer(graph):
    """E：单一失败提示（无文件 / 无正文共用）。"""
    n = find_node(graph, N_ANS_BADFILE)
    if not n:
        raise SystemExit("找不到 %s" % N_ANS_BADFILE)
    n["data"]["title"] = "提示-未能入库"
    n["data"]["answer"] = NEW_BADFILE_ANSWER
    n["data"]["desc"] = "没有文件 或 没提取到正文，都走这里"


# ---- 校验 -----------------------------------------------------------------
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
    # 每个非 start 节点都应有入边
    has_in = {e["target"] for e in graph["edges"]}
    for n in graph["nodes"]:
        if n["id"] == N_START or n["id"] == N_ITER_START:
            continue
        if n["data"].get("type") == "iteration-start":
            continue
        if n["id"] not in has_in:
            bad.append("节点没有入边: %s (%s)" % (n["id"], n["data"].get("title")))
    return bad


def verify_spec(graph):
    issues = []
    if len(graph["nodes"]) != TARGET_NODE_COUNT:
        issues.append("节点数=%d（目标 %d）" % (len(graph["nodes"]), TARGET_NODE_COUNT))
    for nid in DELETE_NODES:
        if find_node(graph, nid):
            issues.append("应删除的节点还在: %s" % nid)

    n = find_node(graph, N_IF_FILESRC)
    if not n:
        issues.append("缺少 %s" % N_IF_FILESRC)
    else:
        cases = n["data"].get("cases") or []
        if len(cases) != 1 or cases[0].get("case_id") != "var":
            issues.append("文件来源判断: cases=%s（应为单个 var case）" % cases)

    n = find_node(graph, N_CHECK)
    if not n or "_flatten" not in (n["data"].get("code") or ""):
        issues.append("校验正文: 缺少新的聚合校验代码")
    elif "text" not in (n["data"].get("outputs") or {}):
        issues.append("校验正文: 缺少 text 输出")

    n = find_node(graph, N_LLM_GEN_QA)
    if n:
        usr = [m.get("text") for m in n["data"]["prompt_template"] if m.get("role") == "user"]
        if usr and ("%s.text" % N_CHECK) not in usr[0]:
            issues.append("生成问答对: 输入仍不是 校验正文.text -> %s" % usr)

    n = find_node(graph, N_HTTP_CREATE)
    if not n:
        issues.append("缺少 %s" % N_HTTP_CREATE)
    elif n["data"].get("error_strategy") != "default-value":
        issues.append("确保知识库存在: error_strategy=%r（应为 default-value）" % n["data"].get("error_strategy"))

    n = find_node(graph, N_DECIDE)
    if not n or "_find_in_list" not in (n["data"].get("code") or ""):
        issues.append("确定知识库ID: 缺少列表/新建二选一的取 id 逻辑")
    elif "list_body" not in json.dumps(n["data"].get("variables"), ensure_ascii=False):
        issues.append("确定知识库ID: 没有接入知识库列表 body")

    n = find_node(graph, N_ANS_BADFILE)
    if n and "%s.reason" % N_CHECK not in (n["data"].get("answer") or ""):
        issues.append("提示-未能入库: 文案没有引用 校验正文.reason")

    n = find_node(graph, N_ITER)
    if not n:
        issues.append("缺少 %s" % N_ITER)
    else:
        if n["data"].get("output_selector") != [N_RETRIEVAL, "result"]:
            issues.append("循环检索: output_selector=%s（应为检索 result）" % n["data"].get("output_selector"))
        if n["data"].get("output_type") != "array[object]":
            issues.append("循环检索: output_type=%r（应为 array[object]）" % n["data"].get("output_type"))

    n = find_node(graph, N_LLM_FINAL)
    if not n:
        issues.append("缺少 %s" % N_LLM_FINAL)
    else:
        ctx = n["data"].get("context") or {}
        if not ctx.get("enabled") or ctx.get("variable_selector") != [N_ITER, "output"]:
            issues.append("去重汇总作答: context=%s（应启用并指向循环输出）" % ctx)
        sysmsg = [m.get("text") for m in n["data"]["prompt_template"] if m.get("role") == "system"]
        if sysmsg and "{{#context#}}" not in sysmsg[0]:
            issues.append("去重汇总作答: system 提示词缺少 {{#context#}}")

    issues += validate_edges(graph)
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
    for n in g["nodes"]:
        print("  %-24s %-22s %s" % (n["id"], n["data"].get("type"), n["data"].get("title")))
    print("-" * 78)
    print("悬空变量引用：%d 处" % len(validate_refs(g)))
    for it in validate_refs(g):
        print("   ", it)
    print("连线校验：%d 处问题" % len(validate_edges(g)))
    for it in validate_edges(g):
        print("   ", it)
    ok, issues = verify_spec(g)
    print("规格自检（22 节点目标）：%s" % ("✅ 通过" if ok else "❌ 未达标"))
    for it in issues:
        print("   -", it)


def cmd_verify():
    g = get_graph()
    ok, issues = verify_spec(g)
    print("草稿规格自检：%s" % ("✅ 通过" if ok else "❌ 不通过"))
    for it in issues:
        print("   -", it)
    raise SystemExit(0 if ok else 1)


def cmd_backup():
    name, path = snapshot("before_slim")
    print("已备份草稿 ->", path)
    return name


def cmd_apply(do_publish=False):
    g = get_graph()
    name, path = snapshot("before_slim")
    before_n = len(g["nodes"])
    print("[1/6] 已备份当前草稿 -> %s（%d 节点）" % (path, before_n))

    merge_a_e(g)
    merge_b(g)
    merge_c(g)
    merge_d(g)
    fix_genqa_input(g)
    fix_badfile_answer(g)
    gone = del_nodes(g, DELETE_NODES)
    print("[2/6] 已完成 5 处合并（A 分支合并 / B 聚合下沉 / C 建库去重 / D 去掉逐问作答 / E 提示合并）")

    bad = validate_refs(g)
    if bad:
        raise SystemExit("图校验失败，存在悬空引用：%s" % bad)
    bad = validate_edges(g)
    if bad:
        raise SystemExit("图校验失败：%s" % bad)
    ok, issues = verify_spec(g)
    print("[3/6] 校验：节点 %d -> %d（删除 %d），悬空引用 0，连线完整，规格自检 %s"
          % (before_n, len(g["nodes"]), gone, "✅ 通过" if ok else "❌ 不通过"))
    for it in issues:
        print("      -", it)
    if not ok:
        raise SystemExit("规格自检不通过，未写库。")

    write_draft(g)
    print("[4/6] 已写回 Dify 草稿")

    if do_publish:
        wid, vnum, ver, wf_type = publish("node-slim-28to22", "精简节点 28->22，行为等价")
        print("[5/6] 已发布 version#%d（%s，type=%s）" % (vnum, wid[:8], wf_type))
    else:
        print("[5/6] 未发布（加 --publish 才生效到运行时的 API/前端）")
    print("[6/6] 回退命令：python wf50_patch.py rollback %s" % name)
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
    if data.get("workflow_id") in (None, ""):
        _psql("UPDATE apps SET workflow_id=NULL, updated_at=now() WHERE id='%s';" % APP_ID)
        print("原应用没有已发布的版本，已把 apps.workflow_id 复位为 NULL")


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
    elif cmd == "backup":
        cmd_backup()
    elif cmd == "apply":
        cmd_apply("--publish" in args)
    elif cmd == "rollback":
        if len(args) < 2:
            raise SystemExit("用法: python wf50_patch.py rollback <快照名>")
        cmd_rollback(args[1])
    elif cmd == "publish":
        wid, vnum, ver, wf_type = publish("manual", "")
        print("已发布 version#%d（%s）" % (vnum, wid[:8]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
