#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""针对 Dify 应用「问答4.0」(app_id caecf6d8-...) 的「写入知识库」改造工具。

目标：知识入库时 **每个 JSON 语句 = 一个分段**（不再被 500 tokens 粘连/切碎）。

做法（自定义分段，改「写入问答对txt」这一个 HTTP 节点）：
    分段标识符   : \\n          —— 一行 = 一段
    分段最大长度 : 4000 characters（Dify 上限；超过才二次切分）
    分段重叠长度 : 0            —— 段间不重叠、不重复
    文本预处理   : 都关闭（保持 JSON 原样，避免把 JSON 里的空格吃掉）
并把「确定知识库ID」里的正文做**规整**：剥掉 markdown 代码围栏、把混排/折行的
JSON 对象重新压成「一行一个 JSON 语句」，保证分段结果与知识点一一对应。

命令（全部可回退）：
  python wf40_patch.py check                 # 只读查看写库/检索节点现状
  python wf40_patch.py backup                # 备份草稿
  python wf40_patch.py apply [--publish]     # 改草稿（可选发布）
  python wf40_patch.py rollback <快照名>      # 用备份还原草稿
  python wf40_patch.py publish               # 把当前草稿发布成新版本
"""
import datetime as dt
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(HERE, "backup")

APP_ID = "caecf6d8-5f31-4c24-b426-ea84d4c7bb02"
APP_NAME = "问答4.0"
PG_CONTAINER = "docker-db_postgres-1"
DB_NAME = "dify"
DB_USER = "postgres"

KB_CREATE_NODE = "node_http_create_kb"     # 新建知识库
KB_WRITE_NODE = "node_http_add_doc"        # 写入问答对txt
DECIDE_NODE = "node_code_decide_id"        # 确定知识库ID（负责正文规整 + 转义）
RETRIEVAL_NODE = "1788759983275"           # 知识检索（只读展示）

# ---- 目标分段规格 -------------------------------------------------------------
SEGMENTATION = {
    "separator": "\n",      # 分段标识符 = 换行（写入 JSON 后是字面 \n，Dify 解析成真换行）
    "max_tokens": 4000,     # 分段最大长度（Dify 允许上限，超过才会二次切分）
    "chunk_overlap": 0,     # 分段重叠长度 = 0，段与段之间不重复
}
PRE_PROCESSING_RULES = [
    {"id": "remove_extra_spaces", "enabled": False},
    {"id": "remove_urls_emails", "enabled": False},
]
PROCESS_RULE = {
    "mode": "custom",
    "rules": {
        "pre_processing_rules": PRE_PROCESSING_RULES,
        "segmentation": SEGMENTATION,
    },
}

# ---- 「确定知识库ID」节点新的代码（新增正文规整；其余逻辑保持原样）----------------
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


def main(find_id: str, create_body: str, text: str) -> dict:
    did = (find_id or "").strip()
    how = "复用已存在的知识库"
    if not did:
        try:
            data = json.loads(create_body or "{}")
        except Exception:
            data = {}
        if isinstance(data, dict):
            did = str(data.get("id") or "")
        how = "新建知识库" if did else "未取到知识库ID（请看节点运行日志）"
    clean = "\\n".join(_pick_json_lines(text))   # 每行一个 JSON 语句 -> 一行 = 一个分段
    return {
        "dataset_id": did,
        "how": how,
        "text_esc": json.dumps(clean, ensure_ascii=False)[1:-1],
        "segments_expected": len(_pick_json_lines(text)),
    }
'''


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
    row = {"graph": _col("graph"), "features": _col("features")}
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = "".join(c for c in label if c.isalnum() or c in "-_") or "snap"
    name = "wf40_%s_%s" % (ts, safe)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    path = os.path.join(BACKUP_DIR, name + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"name": name, "at": ts, "app_id": APP_ID, "app_name": APP_NAME,
                   "graph": json.loads(row["graph"]),
                   "features": json.loads(row["features"])},
                  f, ensure_ascii=False, indent=1)
    return name, path


# ------------------------------------------------------------------ 图操作
def find_node(graph, node_id):
    for n in graph["nodes"]:
        if n["id"] == node_id:
            return n
    return None


def build_add_doc_payload():
    """写入问答对文本：一行一个 JSON 语句 -> 自定义分段按 \\n 切，一行正好一段。"""
    return {
        "name": "{{#node_code_filesrc.filename_esc#}}",
        "text": "{{#node_code_decide_id.text_esc#}}",
        "indexing_technique": "high_quality",
        "process_rule": PROCESS_RULE,
    }


def set_http_json_body(node, obj):
    body = node["data"]["body"]
    assert body.get("type") == "json", "该节点的 body 不是 json 类型"
    body["data"][0]["value"] = json.dumps(obj, ensure_ascii=False)


def fix_if_hasfile(graph):
    """修复「是否有对话附件」if-else 的条件值。

    原条件写的是 `has_file is ""`（空字符串）。has_file 是布尔变量，Dify 在
    归一化期望值时会走 `_convert_to_bool("")` -> `json.loads("")` -> 抛
    `Expecting value: line 1 column 1 (char 0)`，节点直接失败。
    改成 `has_file is false`（语义就是原来的"一个文件都没有"）即可。
    """
    n = find_node(graph, "node_if_hasfile")
    if not n:
        raise SystemExit("找不到 节点 node_if_hasfile（是否有对话附件）")
    changed = False
    for case in n["data"].get("cases") or []:
        for cond in case.get("conditions") or []:
            if cond.get("variable_selector", [None, None])[-1] == "has_file" and cond.get("value") != False:
                cond["value"] = False
                changed = True
    return changed


def verify_spec(graph):
    """检查草稿/版本是否满足本改造的规格，返回 (是否通过, 说明列表)。

    用途：Dify 页面里如果开着**改造前**的工作流，编辑器自动保存会把旧图写回草稿，
    把这里的改动覆盖掉。改完立刻跑一次 verify 就能发现。
    """
    issues = []
    n_write = find_node(graph, KB_WRITE_NODE)
    n_decide = find_node(graph, DECIDE_NODE)
    n_file = find_node(graph, "node_if_hasfile")
    if not n_write or not n_decide:
        return False, ["找不到 node_http_add_doc / node_code_decide_id"]

    txt = n_write["data"]["body"]["data"][0]["value"]
    try:
        pr = json.loads(txt.replace("{{#", "X").replace("#}}", "Y"))["process_rule"]
        seg = pr["rules"]["segmentation"]
        if pr["mode"] != "custom":
            issues.append("写入问答对txt: process_rule.mode=%s（应为 custom）" % pr["mode"])
        if seg.get("separator") != "\n":
            issues.append("写入问答对txt: separator=%r（应为换行）" % seg.get("separator"))
        if seg.get("max_tokens") != 4000 or seg.get("chunk_overlap") != 0:
            issues.append("写入问答对txt: segmentation=%s（应为 4000/0）" % seg)
    except Exception as e:
        issues.append("写入问答对txt: body 解析失败 %s" % e)

    if "_pick_json_lines" not in (n_decide["data"].get("code") or ""):
        issues.append("确定知识库ID: 缺少正文规整（_pick_json_lines）")
    if "segments_expected" not in (n_decide["data"].get("outputs") or {}):
        issues.append("确定知识库ID: 缺少 segments_expected 输出")

    if n_file:
        vals = [c["value"] for c in (n_file["data"].get("cases") or [])[0]["conditions"]]
        if vals != [False]:
            issues.append("是否有对话附件: 条件值=%r（应为 False）" % vals)
    return (not issues), issues


def validate_refs(graph):
    """校验图里不存在指向"不存在的节点 id"的变量引用。"""
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
    for nid, title in ((KB_WRITE_NODE, "写入问答对txt"),
                       (KB_CREATE_NODE, "新建知识库"),
                       (DECIDE_NODE, "确定知识库ID")):
        n = find_node(g, nid)
        if not n:
            print("!! 找不到节点 %s (%s)" % (nid, title))
            continue
        d = n["data"]
        print("=" * 78)
        print("节点 %s  %s   type=%s" % (nid, d.get("title"), d.get("type")))
        if d.get("type") == "http-request":
            print("  url :", d.get("url"))
            print("  body:", d["body"]["data"][0]["value"])
        else:
            print(d.get("code"))
    n = find_node(g, "node_if_hasfile")
    print("=" * 78)
    print("节点 node_if_hasfile  是否有对话附件  type=if-else")
    print(json.dumps({c["case_id"]: c["conditions"] for c in (n["data"].get("cases") or [])},
                     ensure_ascii=False))
    bad = validate_refs(g)
    print("=" * 78)
    print("悬空变量引用：%d 处" % len(bad))
    for it in bad:
        print("   ", it)
    ok, issues = verify_spec(g)
    print("规格自检：%s" % ("✅ 通过" if ok else "❌ 不通过（可能被 Dify 页面里的旧图覆盖了）"))
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
    name, path = snapshot("before_patch")
    print("已备份草稿 ->", path)
    return name


def cmd_apply(do_publish=False):
    g = get_graph()
    name, path = snapshot("before_patch")
    print("[1/5] 已备份当前草稿 -> %s" % path)

    n_write = find_node(g, KB_WRITE_NODE)
    n_decide = find_node(g, DECIDE_NODE)
    if not n_write or not n_decide:
        raise SystemExit("找不到写库/确定ID节点，请先跑 check")

    old_write = n_write["data"]["body"]["data"][0]["value"]
    old_code = n_decide["data"]["code"]

    set_http_json_body(n_write, build_add_doc_payload())
    n_decide["data"]["code"] = NEW_DECIDE_CODE
    n_decide["data"]["outputs"]["segments_expected"] = {"type": "number", "children": None}
    fixed = fix_if_hasfile(g)
    print("[2/5] 已改写「写入问答对txt」分段规则 + 「确定知识库ID」正文规整"
          + ("；并修复「是否有对话附件」条件值" if fixed else "（附件条件值无需改）"))

    # 校验
    txt = n_write["data"]["body"]["data"][0]["value"]
    parsed = json.loads(txt.replace("{{#", "X").replace("#}}", "Y"))
    pr = parsed["process_rule"]
    assert pr["mode"] == "custom", "process_rule.mode 不是 custom"
    seg = pr["rules"]["segmentation"]
    assert seg["separator"] == "\n" and seg["max_tokens"] == 4000 and seg["chunk_overlap"] == 0, seg
    assert n_decide["data"]["code"].startswith("import json")
    bad = validate_refs(g)
    if bad:
        raise SystemExit("图校验失败，存在悬空引用：%s" % bad)
    print("[3/5] 校验通过：custom 分段(\\n/4000/0)、正文规整、图内无悬空引用")

    write_draft(g)
    print("[4/5] 已写回 Dify 草稿")

    if do_publish:
        wid, vnum, ver, wf_type = publish("kb-segmentation", "写入知识库：每个 JSON 语句一段")
        print("[5/5] 已发布 version#%d（%s，type=%s）" % (vnum, wid[:8], wf_type))
    else:
        print("[5/5] 未发布（加 --publish 才生效到应用运行时）")

    print("\n改动前 写入节点 body:\n", old_write)
    print("\n改动前 确定ID 代码前 3 行:\n", "\n".join(old_code.splitlines()[:3]))
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
    elif cmd == "backup":
        cmd_backup()
    elif cmd == "apply":
        cmd_apply("--publish" in args)
    elif cmd == "rollback":
        if len(args) < 2:
            raise SystemExit("用法: python wf40_patch.py rollback <快照名>")
        cmd_rollback(args[1])
    elif cmd == "publish":
        wid, vnum, ver, wf_type = publish("manual", "")
        print("已发布 version#%d（%s）" % (vnum, wid[:8]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
