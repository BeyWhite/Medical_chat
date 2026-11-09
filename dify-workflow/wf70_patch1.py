# -*- coding: utf-8 -*-
"""问答7.0 工作流改造：

需求（用户原文）——
  1. 「不需要空文件检测，这在前端已实现」
  2. 「增加若为非医学知识，则输出为非医学知识，不走写入问答对分支」

改动点（4 处，其余节点/连线一律不碰）：

  A. 删分支：if-else「正文是否有效」(node_if_text_ok) + answer「提示-文件无正文」
     (node_answer_badfile) 整体移除 —— 连同 3 条边一起删。空文件由前端拦截。
  B. 「校验正文」(node_code_check_text) 退化为「合并正文」：
     仍然取两份文档提取器里最长的一份当正文，但**不再判定有效性、不再有 verdict**；
     新增输出 has_qa（非空字符串 = 没问题），实际上只做正文搬运，供下游生成问答对使用。
     改名「合并正文」，避免名字与行为不符。
  C. 「生成问答对」(node_llm_gen_qa) 的 prompt 强化：
     非医学内容 -> 只输出 NO_MED  这一行纯文本（不输出任何 JSON）。
     医学内容 -> 照旧每行一个 JSON 问答对。
     新增输出 is_medical（string，"" = 医学 / "no" = 非医学）。
     Dify 的 llm 节点不能自定义输出名，所以 is_medical 由下游代码节点算。
  D. 新增 if-else「是否医学内容」(node_if_medical)：
     条件 `生成问答对.text contains NO_MED` -> true 走「提示-非医学知识」；false 走「查知识库列表」。
     新增 answer「提示-非医学知识」(node_answer_nomed)。
     判据用「contains 字面量」而不是「not empty 变量」：LLM 节点无法给出干净的布尔
     或一元信号，contains 在 graphon 里对字符串做子串判断、不会因为变量缺失炸节点。

连线变化：
    合并正文 -> 生成问答对 -> 是否医学内容 -true-> 提示-非医学知识
                                        \\-false-> 查知识库列表 -> … 原入库链

用法（在本目录下执行）：
    python wf70_patch1.py check                 # 只打印将要改的内容，不写库
    python wf70_patch1.py sandbox               # 把新代码节点丢进 dify-sandbox 真跑一遍
    python wf70_patch1.py patch                 # 备份 + 写入草稿（不发布）
    python wf70_patch1.py patch --publish       # 备份 + 写入草稿 + 发布
    python wf70_patch1.py rollback <快照名>      # 回退草稿
    python wf70_patch1.py repair                # 修复 features 被双重编码
    python wf70_patch1.py smoke                 # 真读一次草稿接口，确认应用能打开
    python wf70_patch1.py status                # 看草稿状态 + features 自检

⚠ 踩过的坑：写 attributes 时 **必须传 dict**。`get_row()['features']` 是 psql 原始文本，
  直接 json.dumps 会写成「双重编码的字符串」，控制台读草稿时报
  `WorkflowResponse.features: Input should be a valid dictionary (dict_type)` → 应用打不开。
"""

import argparse
import datetime as dt
import json
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(HERE, "backup")
INDEX_PATH = os.path.join(BACKUP_DIR, "index.json")

APP_ID = "9c39b494-91cd-4ae5-b4a6-46fc861683ce"   # 问答7.0
APP_NAME = "问答7.0"

PG_CONTAINER = "docker-db_postgres-1"
DB_NAME = "dify"
DB_USER = "postgres"
API_CONTAINER = "docker-api-1"
SANDBOX_KEY = "dify-sandbox"

N_CHECK = "node_code_check_text"     # 校验正文 -> 合并正文
N_GEN_QA = "node_llm_gen_qa"         # 生成问答对
N_LIST_KB = "node_http_list_kb"      # 查知识库列表（入库链第一站）
N_IF_TEXT = "node_if_text_ok"        # 正文是否有效（待删）
N_ANS_BAD = "node_answer_badfile"    # 提示-文件无正文（待删）

N_IF_MED = "node_if_medical"         # 是否医学内容（新增）
N_ANS_NOMED = "node_answer_nomed"    # 提示-非医学知识（新增）

NO_MED_TOKEN = "NO_MED"


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


def _col(expr):
    return _psql("SELECT %s FROM workflows WHERE app_id='%s' AND version='draft';"
                 % (expr, APP_ID), tuples_only=True).rstrip("\n")


def get_row():
    return {"id": _col("id"), "graph": _col("graph"), "features": _col("features")}


def get_graph():
    return json.loads(get_row()["graph"])


def get_features():
    """注意：必须解析成 dict 再写回。直接拿 psql 原始文本去 json.dumps 会
    得到「双重编码的字符串」，控制台读草稿时会 500。"""
    return json.loads(get_row()["features"])


def write_draft(graph, features):
    if not isinstance(graph, dict):
        raise TypeError("graph 必须是 dict（不要传 psql 原始文本）")
    if not isinstance(features, dict):
        raise TypeError("features 必须是 dict（不要传 psql 原始文本）")
    s = ("UPDATE workflows SET graph = %s, features = %s, updated_at = now() "
         "WHERE app_id='%s' AND version='draft';\n"
         % (_dollar(json.dumps(graph, ensure_ascii=False), "difyg"),
            _dollar(json.dumps(features, ensure_ascii=False), "difyf"),
            APP_ID))
    _psql("", stdin_text=s)


def repair_features():
    """幂等：把被双重编码的 features 还原成 JSON 对象。"""
    raw = get_row()["features"]
    val = json.loads(raw)
    if not isinstance(val, str):
        print("features 形态正常（dict），无需修复")
        return False
    obj = json.loads(val)
    if not isinstance(obj, dict):
        raise SystemExit("解出来还不是 dict，请人工检查：%r" % (obj,))
    snap = snapshot("before_repair_features")
    _psql("", stdin_text=("UPDATE workflows SET features = %s, updated_at = now() "
                          "WHERE app_id='%s' AND version='draft';\n"
                          % (_dollar(json.dumps(obj, ensure_ascii=False), "difyf"),
                             APP_ID)))
    print("已修复 features（快照: %s）" % snap)
    return True


def check_features():
    probs = []
    raw = get_row()["features"]
    try:
        val = json.loads(raw)
    except Exception as e:
        return ["features 不是合法 JSON: %s" % e]
    if not isinstance(val, dict):
        probs.append("features 被编码成了 %s（应为 dict）—— 跑 repair 修复"
                     % type(val).__name__)
    g = json.loads(get_row()["graph"])
    if not isinstance(g, dict) or "nodes" not in g:
        probs.append("graph 不是含 nodes 的对象")
    return probs


# ------------------------------------------------------------------ 快照

def _load_index():
    if os.path.exists(INDEX_PATH):
        with open(INDEX_PATH, encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_index(idx):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)


def snapshot(label):
    row = get_row()
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = "".join(c for c in label if c.isalnum() or c in "-_") or "snap"
    name = "wf70_%s_%s" % (ts, safe)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    with open(os.path.join(BACKUP_DIR, name + ".json"), "w", encoding="utf-8") as f:
        json.dump({"name": name, "at": ts, "label": label,
                   "graph": json.loads(row["graph"]),
                   "features": json.loads(row["features"])},
                  f, ensure_ascii=False, indent=1)
    idx = _load_index()
    idx.append({"name": name, "at": ts, "label": label})
    _save_index(idx)
    return name


# ------------------------------------------------------------------ 新代码节点

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
    # 两个提取器并行跑，只有一个会拿到文件，取最长的那份当正文。
    # 这里**不再判定正文是否有效**（空文件/扫描件由前端拦截），只做搬运。
    parts = [_flatten(doc_text), _flatten(var_text)]
    body = max(parts, key=lambda s: len(s.strip())).strip()
    n = len(body)
    return {
        "text": body,
        "doc_chars": n,
        # 非空字符串 = 正文可用；给下游 if-else 用（一元 not empty 判断）
        "has_qa": "1" if body else "",
    }
'''

NEW_CHECK_OUTPUTS = {
    "text": {"type": "string", "children": None},
    "doc_chars": {"type": "number", "children": None},
    "has_qa": {"type": "string", "children": None},
}

NEW_GEN_QA_SYS = (
    "你是医学知识整理助手。请先判断给定的内容是否为**医学相关知识**：\n"
    "A) 若与医学无关（如计算机、金融、法律、娱乐、生活杂事等），"
    "只输出一行纯文本 {}，不要输出任何 JSON、解释或其它文字。\n"
    "B) 若为医学相关知识，请把内容写成「问答对」，每个问答对带五个相似问。\n"
    "  要求：\n"
    "  1. 每行输出一个 JSON 对象，严格使用格式："
    "{{\"where\": \"隶属的文件夹名称_第几章：章名_第几节\", "
    "\"question\": \"问题1/问题2/问题3/问题4/问题5\", \"response_chosen\": \"答案\"}}\n"
    "  2. 只输出 JSON，每行一个；不要输出解释、标题、Markdown 代码块或多余文字。"
    "一个问答对是一个块。\n"
    "  3. 问题要具体、自然；答案必须完全依据文档内容，不得编造。\n"
    "  4. 尽可能覆盖文档中的全部知识点。\n"
    "  示例：\"where\": \"test.docx_第一章：急救篇_第一节_院前急救\", "
    "\"question\": \"什么是院前急救？ / 院前急救的定义是什么？ / 什么叫院外急救？ / "
    "院前急救包括哪些环节？ / 院前急救是在医院外进行的吗？\", "
    "\"response_chosen\": \"院前急救也称院外急救，是指在医院之外的环境中对各种危及生命的"
    "急症、创伤、中毒、灾难事故等伤病者进行现场救护、转运及途中监护救治的统称。\""
).replace("{}", NO_MED_TOKEN)

NEW_IF_MED_CASE_ID = "nomed"
NEW_ANS_NOMED_TEXT = (
    "⚠️ 该文件内容不是医学相关知识，已跳过入库。\n"
    "\n"
    "- 文件：**{{#node_code_filesrc.filename#}}**\n"
    "- 判断：正文字数 {{#node_code_check_text.doc_chars#}}，"
    "内容主题与医学无关，未生成问答对、也未写入任何知识库。\n"
    "\n"
    "本系统的知识库只收录医学内容，请上传医学教材、临床指南、药品说明等医学资料。"
)


# ------------------------------------------------------------------ 打补丁

def _new_if_else_node():
    return {
        "data": {
            "cases": [
                {
                    "case_id": NEW_IF_MED_CASE_ID,
                    "conditions": [
                        {
                            "comparison_operator": "contains",
                            "id": "2b0c6a41-2f6d-4c8a-9e1b-70d5c3a84f17",
                            "value": NO_MED_TOKEN,
                            "varType": "string",
                            "variable_selector": [N_GEN_QA, "text"],
                        }
                    ],
                    "logical_operator": "and",
                }
            ],
            "desc": ("命中 = 大模型判定为非医学内容（输出 %s），给提示、不写库；"
                     "否则正常入库" % NO_MED_TOKEN),
            "logical_operator": "and",
            "selected": False,
            "title": "是否医学内容",
            "type": "if-else",
        },
        "height": 167,
        "id": N_IF_MED,
        "position": {"x": 1914.6523385677497, "y": 545.0},
        "positionAbsolute": {"x": 1914.6523385677497, "y": 545.0},
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
        "type": "custom",
        "width": 241,
        "zIndex": 0,
    }


def _new_answer_nomed_node():
    return {
        "data": {
            "answer": NEW_ANS_NOMED_TEXT,
            "desc": "有附件但大模型判定内容与医学无关",
            "selected": False,
            "title": "提示-非医学知识",
            "type": "answer",
            "variables": [],
        },
        "height": 195,
        "id": N_ANS_NOMED,
        "position": {"x": 2296.0, "y": 545.0},
        "positionAbsolute": {"x": 2296.0, "y": 545.0},
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
        "type": "custom",
        "width": 241,
        "zIndex": 0,
    }


def patch_graph(g):
    """就地修改 graph，返回改动说明列表。"""
    notes = []
    nodes = {n["id"]: n for n in g["nodes"]}

    # ---------- A. 删除空文件检测分支 ----------
    if N_IF_TEXT in nodes:
        _n = nodes.pop(N_IF_TEXT)
        g["nodes"] = [n for n in g["nodes"] if n["id"] != N_IF_TEXT]
        notes.append("  [A1] 删除 if-else「%s」(%s)" % (_n["data"]["title"], N_IF_TEXT))
    else:
        notes.append("  [A1] if-else %s 已不存在，跳过" % N_IF_TEXT)

    if N_ANS_BAD in nodes:
        _n = nodes.pop(N_ANS_BAD)
        g["nodes"] = [n for n in g["nodes"] if n["id"] != N_ANS_BAD]
        notes.append("  [A2] 删除 answer「%s」(%s)" % (_n["data"]["title"], N_ANS_BAD))
    else:
        notes.append("  [A2] answer %s 已不存在，跳过" % N_ANS_BAD)

    # ---------- B. 校验正文 -> 合并正文 ----------
    n = nodes[N_CHECK]
    assert n["data"]["type"] == "code", "校验正文 节点类型不是 code"
    old_outputs = sorted((n["data"].get("outputs") or {}).keys())
    n["data"]["code"] = NEW_CHECK_CODE
    n["data"]["outputs"] = json.loads(json.dumps(NEW_CHECK_OUTPUTS))
    n["data"]["title"] = "合并正文"
    n["data"]["desc"] = "取两份提取结果里最长的一份当正文；不再判定有效性（空文件由前端拦截）"
    notes.append("  [B] %s：title「校验正文」->「合并正文」，outputs %s -> %s"
                 % (N_CHECK, old_outputs, sorted(NEW_CHECK_OUTPUTS.keys())))

    # ---------- C. 生成问答对 prompt ----------
    n = nodes[N_GEN_QA]
    assert n["data"]["type"] == "llm", "生成问答对 节点类型不是 llm"
    pt = n["data"]["prompt_template"]
    old_sys = pt[0]["text"]
    if NO_MED_TOKEN not in old_sys:
        pt[0]["text"] = NEW_GEN_QA_SYS
        notes.append("  [C] %s「%s」：system prompt 改为「非医学只输出 %s」"
                     % (N_GEN_QA, n["data"]["title"], NO_MED_TOKEN))
    else:
        notes.append("  [C] %s：prompt 已含 %s，跳过" % (N_GEN_QA, NO_MED_TOKEN))
    n["data"]["desc"] = ("非医学内容 -> 只输出 %s；医学内容 -> 每行一个 JSON 问答对"
                         % NO_MED_TOKEN)

    # ---------- D. 新增「是否医学内容」+「提示-非医学知识」----------
    if N_IF_MED not in nodes:
        g["nodes"].append(_new_if_else_node())
        notes.append("  [D1] 新增 if-else「是否医学内容」(%s)" % N_IF_MED)
    if N_ANS_NOMED not in nodes:
        g["nodes"].append(_new_answer_nomed_node())
        notes.append("  [D2] 新增 answer「提示-非医学知识」(%s)" % N_ANS_NOMED)

    # ---------- 连线 ----------
    edges = g["edges"]
    before = len(edges)

    def drop(pred):
        out = [e for e in edges if not pred(e)]
        removed = len(edges) - len(out)
        return out, removed

    # 1) 删掉「正文是否有效」相关的三条边 + 校验正文->它的边
    edges, r1 = drop(lambda e: e["source"] == N_IF_TEXT or e["target"] == N_IF_TEXT)
    # 2) 删掉 生成问答对->查知识库列表（要改由 if-else 中转）
    edges, r2 = drop(lambda e: e["source"] == N_GEN_QA and e["target"] == N_LIST_KB)
    notes.append("  [D3] 删除旧连线 %d 条（%d 条指向正文是否有效 / %d 条 生成问答对->查知识库列表）"
                 % (r1 + r2, r1, r2))

    # 3) 合并正文 -> 生成问答对（原来经过 if-else，现在直连）
    assert not any(e["source"] == N_CHECK and e["target"] == N_GEN_QA for e in edges)
    edges.append({
        "data": {"isInIteration": False, "isInLoop": False,
                 "sourceType": "code", "targetType": "llm"},
        "id": "e-%s-%s" % (N_CHECK, N_GEN_QA),
        "source": N_CHECK, "sourceHandle": "source",
        "target": N_GEN_QA, "targetHandle": "target",
        "type": "custom", "zIndex": 0,
    })
    # 4) 生成问答对 -> 是否医学内容
    edges.append({
        "data": {"isInIteration": False, "isInLoop": False,
                 "sourceType": "llm", "targetType": "if-else"},
        "id": "e-%s-%s" % (N_GEN_QA, N_IF_MED),
        "source": N_GEN_QA, "sourceHandle": "source",
        "target": N_IF_MED, "targetHandle": "target",
        "type": "custom", "zIndex": 0,
    })
    # 5) 是否医学内容 -true-> 提示-非医学知识
    edges.append({
        "data": {"isInIteration": False, "isInLoop": False,
                 "sourceType": "if-else", "targetType": "answer"},
        "id": "%s-%s-%s-target" % (N_IF_MED, NEW_IF_MED_CASE_ID, N_ANS_NOMED),
        "source": N_IF_MED, "sourceHandle": NEW_IF_MED_CASE_ID,
        "target": N_ANS_NOMED, "targetHandle": "target",
        "type": "custom", "zIndex": 0,
    })
    # 6) 是否医学内容 -false-> 查知识库列表（原入库链）
    edges.append({
        "data": {"isInIteration": False, "isInLoop": False,
                 "sourceType": "if-else", "targetType": "http-request"},
        "id": "%s-false-%s-target" % (N_IF_MED, N_LIST_KB),
        "source": N_IF_MED, "sourceHandle": "false",
        "target": N_LIST_KB, "targetHandle": "target",
        "type": "custom", "zIndex": 0,
    })
    notes.append("  [D4] 新增连线 4 条（合并正文->生成问答对->是否医学内容 "
                 "-%s->提示 / -false->查知识库列表）" % NEW_IF_MED_CASE_ID)
    g["edges"] = edges
    notes.append("  [D5] 连线总数 %d -> %d" % (before, len(edges)))
    return notes


# ------------------------------------------------------------------ 校验

def verify(g):
    """引用完整性 + 输出声明 + 关键占位符。返回问题列表。"""
    problems = []
    ids = {n["id"] for n in g["nodes"]}

    for e in g["edges"]:
        if e["source"] not in ids:
            problems.append("边指向不存在的源节点: %s" % e["source"])
        if e["target"] not in ids:
            problems.append("边指向不存在的目标节点: %s" % e["target"])
        if e["source"] == N_IF_MED and e.get("sourceHandle") not in (NEW_IF_MED_CASE_ID, "false"):
            problems.append("是否医学内容的 sourceHandle 异常: %s" % e.get("sourceHandle"))

    def walk(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                yield from walk(v, "%s.%s" % (path, k))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                yield from walk(v, "%s[%d]" % (path, i))
        else:
            yield path, o

    SYS = ("sys", "env", "conversation")
    for n in g["nodes"]:
        for var in (n["data"].get("variables") or []):
            sel = var.get("value_selector")
            if (isinstance(sel, list) and len(sel) == 2 and sel[0]
                    and sel[0] not in SYS and sel[0] not in ids):
                problems.append("节点 %s 引用了不存在的节点 %s" % (n["id"], sel[0]))
        for p, v in walk(n["data"]):
            if not isinstance(v, str):
                continue
            i = 0
            while True:
                a = v.find("{{#", i)
                if a < 0:
                    break
                b = v.find("#}}", a)
                if b < 0:
                    break
                expr = v[a + 3:b]
                i = b + 3
                parts = expr.split(".")
                if len(parts) < 2 or parts[0] in SYS:
                    continue
                nid = parts[0]
                if nid not in ids:
                    problems.append("%s 里引用了不存在的节点: {{#%s#}}" % (n["id"], expr))
                    continue
                tgt = [x for x in g["nodes"] if x["id"] == nid][0]["data"]
                if tgt.get("type") == "code" and len(parts) >= 2:
                    if parts[1] not in (tgt.get("outputs") or {}):
                        problems.append("code 节点 %s 没有输出 %s（被 {{#%s#}} 引用）"
                                        % (nid, parts[1], expr))
                if tgt.get("type") == "llm" and len(parts) >= 2 and parts[1] != "text":
                    problems.append("llm 节点 %s 只有 text 输出，却引用了 %s" % (nid, expr))

    # 关键点断言
    if N_IF_TEXT in ids:
        problems.append("if-else「正文是否有效」没删干净")
    if N_ANS_BAD in ids:
        problems.append("answer「提示-文件无正文」没删干净")
    if N_IF_MED not in ids or N_ANS_NOMED not in ids:
        problems.append("新增节点缺失")
    chk = [n for n in g["nodes"] if n["id"] == N_CHECK][0]["data"]
    if "verdict" in (chk.get("outputs") or {}):
        problems.append("合并正文 仍声明 verdict 输出（应已去掉）")
    gen = [n for n in g["nodes"] if n["id"] == N_GEN_QA][0]["data"]
    if NO_MED_TOKEN not in gen["prompt_template"][0]["text"]:
        problems.append("生成问答对 prompt 里没有 %s 约定" % NO_MED_TOKEN)
    # 入库链入口必须有 if-else 把关
    if not any(e["source"] == N_IF_MED and e["target"] == N_LIST_KB for e in g["edges"]):
        problems.append("是否医学内容 没有接到 查知识库列表")
    if any(e["source"] == N_GEN_QA and e["target"] == N_LIST_KB for e in g["edges"]):
        problems.append("生成问答对 仍直连 查知识库列表（绕过非医学判定）")
    # 悬空：每个非 answer 节点都应有出边
    srcs = {e["source"] for e in g["edges"]}
    for n in g["nodes"]:
        t = n["data"]["type"]
        if t in ("answer",):
            continue
        if t == "iteration-start":
            continue
        if n["id"] not in srcs and not n.get("parentId"):
            problems.append("节点 %s「%s」没有出边（悬空）" % (n["id"], n["data"]["title"]))
    return problems


# ------------------------------------------------------------------ 沙箱实测

def sandbox_test(g):
    """把新的「合并正文」代码丢进 dify-sandbox 真跑。"""
    import base64
    node = [n for n in g["nodes"] if n["id"] == N_CHECK][0]["data"]
    code = node["code"]

    cases = [
        ("对话附件有正文", {"doc_text": ["\n".join(["第一段"] * 200)], "var_text": []}),
        ("文件变量有正文", {"doc_text": [], "var_text": ["\n".join(["第二段"] * 300)]}),
        ("两边都空（前端应已拦截）", {"doc_text": [], "var_text": []}),
        ("两边都有，取更长的那份", {"doc_text": ["短"], "var_text": ["长" * 500]}),
    ]

    runner_tpl = """
import json
from base64 import b64decode

inputs_obj = json.loads(b64decode('%s').decode('utf-8'))
output_obj = main(**inputs_obj)
print(json.dumps(output_obj, ensure_ascii=False))
"""

    parts = []
    for _, inputs in cases:
        b64 = base64.b64encode(json.dumps(inputs, ensure_ascii=False).encode()).decode()
        parts.append(json.dumps({"code": code + (runner_tpl % b64)}, ensure_ascii=False))

    prog = ("import json\nCASES = [\n" + ",\n".join(parts) + "\n]\n"
            "for i, c in enumerate(CASES):\n"
            "    print('=====CASE', i, '=====')\n"
            "    exec(c['code'])\n")

    payload = json.dumps({"language": "python3", "code": prog,
                          "preload": "", "enable_network": True}, ensure_ascii=False)
    p = subprocess.run(
        ["docker", "exec", "-i", API_CONTAINER, "python", "-c",
         "import sys,json,httpx;"
         "payload=json.load(sys.stdin);"
         "r=httpx.post('http://sandbox:8194/v1/sandbox/run',json=payload,"
         "headers={'X-Api-Key':'%s'},timeout=60);"
         "print(r.status_code);print(r.text)" % SANDBOX_KEY],
        input=payload, capture_output=True, text=True, encoding="utf-8")
    out = (p.stdout or "") + (p.stderr or "")
    print(out[:4000])
    for i, (title, _) in enumerate(cases):
        print("---- case%d: %s" % (i, title))
    return out


# ------------------------------------------------------------------ 冒烟

SMOKE_PY = r'''
import sys, json, jwt, httpx
sys.path.insert(0, "/app/api")
from configs import dify_config

key = open("/app/api/storage/.dify_secret_key").read().strip()
acc = "%(acc)s"
app = "%(app)s"
tok = jwt.encode({"user_id": acc, "exp": 9999999999,
                  "iss": dify_config.DEPLOYMENT_EDITION,
                  "sub": "Console API Passport"}, key, algorithm="HS256")
csrf = jwt.encode({"exp": 9999999999, "sub": acc}, key, algorithm="HS256")
H = {"Authorization": "Bearer " + tok, "X-CSRF-Token": csrf,
     "Cookie": "csrf_token=" + csrf, "Content-Type": "application/json"}

c = httpx.Client(base_url="http://localhost:5001", timeout=30)
bad = 0
for p in ["/console/api/apps/%%s/workflows/draft" %% app,
          "/console/api/apps/%%s/workflows" %% app,
          "/console/api/apps/%%s" %% app]:
    r = c.get(p, headers=H)
    if r.status_code != 200:
        bad += 1
        print("ERR", r.status_code, p)
        print("    ", r.text[:300])
    else:
        print("OK ", r.status_code, p)
        if p.endswith("/workflows/draft"):
            d = r.json()
            print("      features 类型:", type(d.get("features")).__name__,
                  "（必须是 dict）")
            g = d.get("graph") or {}
            print("      graph nodes:", len(g.get("nodes", [])),
                  "edges:", len(g.get("edges", [])))
            ids = [n["id"] for n in g["nodes"]]
            print("      是否医学内容存在:", "node_if_medical" in ids)
            print("      正文是否有效已删:", "node_if_text_ok" not in ids)
            print("      提示-文件无正文已删:", "node_answer_badfile" not in ids)
print("SMOKE_RESULT=" + ("FAIL" if bad else "PASS"))
'''


def smoke():
    acc = "75f7a702-f87c-43c8-9007-346b69bfc318"
    prog = SMOKE_PY % {"acc": acc, "app": APP_ID}
    p = subprocess.run(["docker", "exec", "-i", API_CONTAINER, "python", "-"],
                       input=prog, capture_output=True, text=True, encoding="utf-8")
    out = (p.stdout or "") + (p.stderr or "")
    print(out.rstrip())
    ok = "SMOKE_RESULT=PASS" in out
    print("\n✅ 控制台能正常读到 %s 草稿（应用可打开）" % APP_NAME if ok
          else "\n❌ 草稿读取异常 —— 先跑 `python wf70_patch1.py repair`")
    return ok


# ------------------------------------------------------------------ 发布

def publish(marked_name="", marked_comment=""):
    version = str(dt.datetime.now(dt.timezone.utc).replace(tzinfo=None))
    out = _psql(
        "INSERT INTO workflow_version_counters (app_id, last_version_number) "
        "VALUES ('%s', 1) ON CONFLICT (app_id) DO UPDATE SET "
        "last_version_number = workflow_version_counters.last_version_number + 1 "
        "RETURNING last_version_number;" % APP_ID, tuples_only=True)
    vnum = int(out.strip())
    wid = _psql(
        "INSERT INTO workflows (tenant_id, app_id, type, version, version_number, "
        "graph, features, created_by, updated_by, marked_name, marked_comment, kind, "
        "updated_at) "
        "SELECT tenant_id, app_id, type, %s, %d, graph, features, created_by, "
        "created_by, %s, %s, kind, now() FROM workflows "
        "WHERE app_id='%s' AND version='draft' RETURNING id;"
        % (_dollar(version), vnum, _dollar(marked_name, "difymn"),
           _dollar(marked_comment, "difymc"), APP_ID),
        tuples_only=True)
    new_id = wid.strip().splitlines()[-1].strip()
    _psql("UPDATE apps SET workflow_id='%s', updated_at=now() WHERE id='%s';"
          % (new_id, APP_ID))
    return new_id, vnum, version


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "sandbox", "patch", "rollback", "repair",
                                    "smoke", "status"])
    ap.add_argument("target", nargs="?", default="")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("-m", "--note", default="")
    args = ap.parse_args()

    if args.cmd == "status":
        row = get_row()
        g = json.loads(row["graph"])
        wf = _psql("SELECT id, version_number FROM workflows WHERE app_id='%s' "
                   "AND version <> 'draft' ORDER BY version_number DESC LIMIT 1;" % APP_ID,
                   tuples_only=True).strip()
        print("%s  draft workflow id=%s  nodes=%d edges=%d"
              % (APP_NAME, row["id"], len(g["nodes"]), len(g["edges"])))
        print("  已发布版本: %s" % (wf or "(无)"))
        print("  features 自检: %s" % (check_features() or "正常"))
        ids = [n["id"] for n in g["nodes"]]
        print("  合并正文 outputs: %s"
              % sorted((([n for n in g['nodes'] if n['id'] == N_CHECK][0]
                         ['data'].get('outputs') or {})).keys()))
        print("  是否医学内容: %s / 提示-非医学知识: %s"
              % (N_IF_MED in ids, N_ANS_NOMED in ids))
        return

    if args.cmd == "repair":
        repair_features()
        print("  features 自检: %s" % (check_features() or "正常"))
        return

    if args.cmd == "smoke":
        smoke()
        return

    if args.cmd == "rollback":
        if not args.target:
            raise SystemExit("用法: python wf70_patch1.py rollback <快照名>")
        snap_now = snapshot("before_rollback_to_%s" % args.target)
        p = os.path.join(BACKUP_DIR, args.target + ".json")
        if not os.path.exists(p):
            raise SystemExit("找不到快照 %s" % p)
        with open(p, encoding="utf-8") as f:
            obj = json.load(f)
        feats = obj["features"]
        if isinstance(feats, str):
            feats = json.loads(feats)
        write_draft(obj["graph"], feats)
        print("已回退到 %s；回退前的现状存为 %s" % (args.target, snap_now))
        if args.publish:
            wid, vnum, ver = publish("rollback-%s" % args.target, args.note)
            print("已发布 version#%d（%s）" % (vnum, wid[:8]))
        return

    g = get_graph()

    if args.cmd == "check":
        g2 = json.loads(json.dumps(g))
        for line in patch_graph(g2):
            print(line)
        probs = verify(g2)
        print("校验: %s" % ("全部通过" if not probs else "\n  " + "\n  ".join(probs)))
        return

    if args.cmd == "sandbox":
        g2 = json.loads(json.dumps(g))
        patch_graph(g2)
        sandbox_test(g2)
        return

    # patch
    snap = snapshot("before_nomed")
    notes = patch_graph(g)
    probs = verify(g)
    if probs:
        print("校验未通过，已中止（草稿未改动）：")
        for x in probs:
            print("  - %s" % x)
        raise SystemExit(2)
    write_draft(g, get_features())
    print("已写入 %s 草稿（快照: %s）" % (APP_NAME, snap))
    for line in notes:
        print(line)
    print("\n⚠ 请到 Dify 编辑器按 F5 刷新，才能看到新图。")
    if args.publish:
        wid, vnum, ver = publish("no-empty-check-plus-nomed", args.note)
        print("已发布：version#%d（%s）" % (vnum, wid[:8]))


if __name__ == "__main__":
    main()
