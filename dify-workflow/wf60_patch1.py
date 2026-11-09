# -*- coding: utf-8 -*-
"""问答6.0「传入知识库」分支改造：

    需求 —— 若知识库已存在，新添加的内容放入同一个知识库下的（新）文档，
            不再因为「文档名 = 文件名」而被 create-by-text 的 upsert 覆盖。

改动点（只动 3 个节点，其余节点/连线一律不碰）：

  1) node_code_decide_id「确定知识库ID」
       新增输出 doc_name / doc_name_esc / kb_existed：
         知识库已存在  -> 文档名 = 「文件名_YYYYMMDD-HHMMSS」→ 新文档，旧文档保留
         知识库本次新建 -> 文档名 = 「文件名」→ 第一份文档保持干净
  2) node_http_add_doc「写入问答对txt」
       body 里 "name" 由 {{#node_code_filesrc.filename_esc#}}
                改为 {{#node_code_decide_id.doc_name_esc#}}
  3) node_answer_file「文件分支-回复」
       回复里显示本次新增的文档名，避免误以为覆盖了旧文档

用法（在本目录下执行）：
    python wf60_patch1.py check                 # 只打印将要改的内容，不写库
    python wf60_patch1.py sandbox               # 把新代码节点丢进 dify-sandbox 真跑一遍
    python wf60_patch1.py patch                 # 备份 + 写入草稿（不发布）
    python wf60_patch1.py patch --publish       # 备份 + 写入草稿 + 发布
    python wf60_patch1.py rollback <快照名>      # 回退草稿
    python wf60_patch1.py repair                # 修复 features 被双重编码（改完必须跑 status 确认）
    python wf60_patch1.py smoke                 # 真读一次草稿接口，确认应用能打开
    python wf60_patch1.py status                # 看草稿状态 + features 自检

⚠ 踩过的坑：写 attributes 时 **必须传 dict**。`get_row()['features']` 是 psql 原始文本，
  直接 json.dumps 会写成「双重编码的字符串」，控制台读草稿时报
  `WorkflowResponse.features: Input should be a valid dictionary (dict_type)` → 应用打不开。
"""

import argparse
import datetime as dt
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(HERE, "backup")
INDEX_PATH = os.path.join(BACKUP_DIR, "index.json")

APP_ID = "0b48b2e3-ec3f-4272-9301-86a6a786d9b3"   # 问答6.0
APP_NAME = "问答6.0"

PG_CONTAINER = "docker-db_postgres-1"
DB_NAME = "dify"
DB_USER = "postgres"
API_CONTAINER = "docker-api-1"
SANDBOX_CONTAINER = "docker-sandbox-1"
SANDBOX_KEY = "dify-sandbox"

NODE_DECIDE = "node_code_decide_id"
NODE_ADD_DOC = "node_http_add_doc"
NODE_ANSWER = "node_answer_file"


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
    """注意：必须解析成 dict 再写回。直接拿 get_row()['features']（psql 原始文本）
    去 json.dumps 会得到「双重编码的字符串」，控制台读草稿时会 500：
        pydantic ValidationError: WorkflowResponse.features
        Input should be a valid dictionary [type=dict_type, input_type=str]
    """
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
    obj = json.loads(val)          # 再解一层
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
    """返回问题列表（空 = 正常）。"""
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
    name = "wf60_%s_%s" % (ts, safe)
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

NEW_DECIDE_CODE = '''import datetime
import json

_TZ = datetime.timezone(datetime.timedelta(hours=8))   # 沙箱容器是 UTC，展示名按东八区


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


def _doc_name(base: str, kb_existed: bool) -> str:
    """决定这次要写进知识库的文档名。

    知识库已存在 -> 「文件名_20261014-153009」：新内容落成该库下的一个新文档，
                    不会和库里的旧文档重名，因此不会被 create-by-text 覆盖；
    知识库本次新建 -> 直接用文件名，第一份文档的名字保持干净。
    """
    base = (base or "医学知识文件").strip()[:200]
    if not kb_existed:
        return base
    ts = datetime.datetime.now(_TZ).strftime("%Y%m%d-%H%M%S")
    return "%s_%s" % (base, ts)


def main(list_body: str, create_body: str, filename: str, text: str) -> dict:
    name = (filename or "").strip()
    did = _find_in_list(list_body, name)      # 已存在 -> 复用（新建那步会拿到 409，落默认值）
    kb_existed = bool(did)
    how = "复用已存在的知识库"
    if not did:                               # 不存在 -> 用「新建知识库」返回的 id
        try:
            data = json.loads(create_body or "{}")
        except Exception:
            data = {}
        if isinstance(data, dict):
            did = str(data.get("id") or "")
        how = "新建知识库" if did else "未取到知识库ID（请看节点运行日志）"

    doc = _doc_name(name, kb_existed)
    lines = _pick_json_lines(text)            # 每行一个 JSON 语句 -> 一行 = 一个分段
    return {
        "dataset_id": did,
        "how": how,
        "kb_existed": "yes" if kb_existed else "no",
        "doc_name": doc,
        "doc_name_esc": json.dumps(doc, ensure_ascii=False)[1:-1],
        "text_esc": json.dumps("\\n".join(lines), ensure_ascii=False)[1:-1],
        "segments_expected": len(lines),
    }
'''

NEW_DECIDE_OUTPUTS = {
    "dataset_id": {"type": "string", "children": None},
    "how": {"type": "string", "children": None},
    "kb_existed": {"type": "string", "children": None},
    "doc_name": {"type": "string", "children": None},
    "doc_name_esc": {"type": "string", "children": None},
    "text_esc": {"type": "string", "children": None},
    "segments_expected": {"type": "number", "children": None},
}

NEW_ANSWER_TEXT = (
    "已处理文件《{{#node_code_filesrc.filename#}}》\n"
    "\n"
    "- 知识库：**{{#node_code_filesrc.filename#}}**（{{#node_code_decide_id.how#}}）\n"
    "- 知识库 ID：`{{#node_code_decide_id.dataset_id#}}`\n"
    "- 本次写入的文档：**{{#node_code_decide_id.doc_name#}}**\n"
    "- 生成的问答对：\n"
    "```\n"
    "{{#node_llm_gen_qa.text#}}\n"
    "```"
)


# ------------------------------------------------------------------ 打补丁

def patch_graph(g):
    """就地修改 graph，返回改动说明列表。"""
    notes = []
    nodes = {n["id"]: n for n in g["nodes"]}

    # 1) 确定知识库ID
    n = nodes[NODE_DECIDE]
    assert n["data"]["type"] == "code", "节点类型不是 code，先确认是不是 6.0 的草稿"
    old_outputs = sorted(n["data"]["outputs"].keys())
    n["data"]["code"] = NEW_DECIDE_CODE
    n["data"]["outputs"] = json.loads(json.dumps(NEW_DECIDE_OUTPUTS))
    n["data"]["desc"] = ("按文件名在知识库列表里命中就复用；否则用新建返回的 id。"
                         "知识库已存在时把文档名改成「文件名_时间戳」，"
                         "让新内容作为该库下的新文档写入、不覆盖旧文档；顺便转义正文。")
    notes.append("  [1] %s「%s」：outputs %s -> %s"
                 % (NODE_DECIDE, n["data"]["title"], old_outputs,
                    sorted(NEW_DECIDE_OUTPUTS.keys())))

    # 2) 写入问答对txt：文档名换成带后缀的
    n = nodes[NODE_ADD_DOC]
    body = n["data"]["body"]
    old_val = body["data"][0]["value"]
    if "node_code_decide_id.doc_name_esc" not in old_val:
        assert "node_code_filesrc.filename_esc" in old_val, "没找到要替换的文档名占位符"
        body["data"][0]["value"] = old_val.replace(
            "{{#node_code_filesrc.filename_esc#}}", "{{#node_code_decide_id.doc_name_esc#}}")
        notes.append("  [2] %s「%s」：body.name %s -> %s"
                     % (NODE_ADD_DOC, n["data"]["title"],
                        "node_code_filesrc.filename_esc", "node_code_decide_id.doc_name_esc"))
    else:
        notes.append("  [2] %s「%s」：已经是 doc_name_esc，跳过"
                     % (NODE_ADD_DOC, n["data"]["title"]))
    n["data"]["desc"] = ("写入问答对：自定义分段 换行/4000/重叠50，预处理替换连续空格换行制表符 "
                         "-> 一行一个 JSON 即一段。文档名用「确定知识库ID」给的 doc_name，"
                         "知识库已存在时带时间戳后缀，保证是新文档而非覆盖旧文档。")

    # 3) 回复里带上文档名
    n = nodes[NODE_ANSWER]
    n["data"]["answer"] = NEW_ANSWER_TEXT
    notes.append("  [3] %s「%s」：回复文案加上「本次写入的文档」" % (NODE_ANSWER, n["data"]["title"]))
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
                if len(parts) < 2 or parts[0] in ("sys", "env", "conversation"):
                    continue
                nid = parts[0]
                if nid not in ids:
                    problems.append("%s 里引用了不存在的节点: {{#%s#}}" % (n["id"], expr))
                    continue
                # 校验输出名是否存在（只对 code / http / 已知节点做，其它节点输出名多，跳过）
                tgt = [x for x in g["nodes"] if x["id"] == nid][0]["data"]
                if tgt.get("type") == "code" and len(parts) >= 2:
                    if parts[1] not in (tgt.get("outputs") or {}):
                        problems.append("code 节点 %s 没有输出 %s（被 {{#%s#}} 引用）"
                                        % (nid, parts[1], expr))

    # 关键点：写入节点必须用新文档名占位符
    add = [n for n in g["nodes"] if n["id"] == NODE_ADD_DOC][0]["data"]
    if "{{#node_code_decide_id.doc_name_esc#}}" not in add["body"]["data"][0]["value"]:
        problems.append("写入问答对txt 的 body.name 没换成 doc_name_esc")
    dec = [n for n in g["nodes"] if n["id"] == NODE_DECIDE][0]["data"]
    if "doc_name_esc" not in (dec.get("outputs") or {}):
        problems.append("确定知识库ID 没有声明 doc_name_esc 输出")
    return problems


# ------------------------------------------------------------------ 沙箱实测

def sandbox_test(g):
    """把新的「确定知识库ID」代码丢进 dify-sandbox 真跑三种场景。"""
    import base64
    node = [n for n in g["nodes"] if n["id"] == NODE_DECIDE][0]["data"]
    code = node["code"]

    cases = [
        ("知识库不存在（新建）", {
            "list_body": json.dumps({"data": []}, ensure_ascii=False),
            "create_body": json.dumps({"id": "kb-new-0001"}, ensure_ascii=False),
            "filename": "test.docx",
            "text": '{"question":"a","response_chosen":"b"}\n{"question":"c","response_chosen":"d"}',
        }),
        ("知识库已存在（复用）", {
            "list_body": json.dumps({"data": [{"id": "kb-old-9999", "name": "test.docx"}]},
                                    ensure_ascii=False),
            "create_body": "{}",           # 新建那步 409 -> default-value 给了 {}
            "filename": "test.docx",
            "text": '{"question":"e","response_chosen":"f"}',
        }),
        ("知识库已存在 + 模型输出带围栏/折行", {
            "list_body": json.dumps({"data": [{"id": "kb-old-9999", "name": "内科.docx"}]},
                                    ensure_ascii=False),
            "create_body": "{}",
            "filename": "内科.docx",
            "text": '```json\n{\n  "where": "内科.docx_第一章：呼吸_第一节",\n'
                    '  "question": "什么是肺炎？",\n  "response_chosen": "肺部炎症"\n}\n```',
        }),
    ]

    runner_tpl = """
import json
from base64 import b64decode

inputs_obj = json.loads(b64decode('%s').decode('utf-8'))
output_obj = main(**inputs_obj)
output_json = json.dumps(output_obj, indent=4)
result = f'''<<RESULT>>{output_json}<<RESULT>>'''
print(result)
"""

    script = []
    for _, inputs in cases:
        b64 = base64.b64encode(json.dumps(inputs, ensure_ascii=False).encode("utf-8")).decode()
        script.append(json.dumps({"code": code + (runner_tpl % b64)}, ensure_ascii=False))

    # 一次性把三个用例串成一个 python 程序发给沙箱，减少往返
    prog = ("import json\n"
            "CASES = [\n" + ",\n".join(script) + "\n]\n"
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


# ------------------------------------------------------------------ 冒烟（真读控制台接口）

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
            print("      graph nodes:", len((d.get("graph") or {}).get("nodes", [])))
            dec = [n for n in d["graph"]["nodes"] if n["id"] == "node_code_decide_id"][0]
            print("      确定知识库ID outputs:", sorted(dec["data"]["outputs"].keys()))
print("SMOKE_RESULT=" + ("FAIL" if bad else "PASS"))
'''


def smoke():
    """真的用控制台 API 读一遍 6.0 草稿 —— 「应用打不开」就靠这个提前发现。"""
    acc = "75f7a702-f87c-43c8-9007-346b69bfc318"
    prog = SMOKE_PY % {"acc": acc, "app": APP_ID}
    p = subprocess.run(["docker", "exec", "-i", API_CONTAINER, "python", "-"],
                       input=prog, capture_output=True, text=True, encoding="utf-8")
    out = (p.stdout or "") + (p.stderr or "")
    print(out.rstrip())
    ok = "SMOKE_RESULT=PASS" in out
    if ok:
        print("\n✅ 控制台能正常读到 6.0 草稿（应用可打开）")
    else:
        print("\n❌ 草稿读取异常 —— 先跑 `python wf60_patch1.py repair`")
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
        dec = [n for n in g["nodes"] if n["id"] == NODE_DECIDE][0]["data"]
        print("  确定知识库ID outputs: %s" % sorted((dec.get("outputs") or {}).keys()))
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
            raise SystemExit("用法: python wf60_patch1.py rollback <快照名>")
        snap_now = snapshot("before_rollback_to_%s" % args.target)
        p = os.path.join(BACKUP_DIR, args.target + ".json")
        if not os.path.exists(p):
            raise SystemExit("找不到快照 %s" % p)
        with open(p, encoding="utf-8") as f:
            obj = json.load(f)
        feats = obj["features"]
        if isinstance(feats, str):      # 兼容老的、被双重编码过的快照
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
    snap = snapshot("before_docfix")
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
        wid, vnum, ver = publish("docfix-同库新文档", args.note)
        print("已发布：version#%d（%s）" % (vnum, wid[:8]))


if __name__ == "__main__":
    main()
