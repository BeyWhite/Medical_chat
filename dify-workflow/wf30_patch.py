#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""针对 Dify 应用「问答3.0」(app_id 37d7e389-...) 的「写入知识库」配置修正工具。

作用（全部可回退）：
  1. backup  —— 把当前草稿完整快照到 backup/wf30_<时间戳>_before_patch.json
  2. apply   —— 把工作流里两个"写库"节点的分段规则显式设为下面这套规格：
        分段标识符   : \n\n
        分段最大长度 : 1024 characters
        分段重叠长度 : 50 characters
        文本预处理   : 替换掉连续的空格、换行符和制表符 (remove_extra_spaces)
      · 节点「新建知识库」   -> 建库请求里补上 process_rule（原先没传，吃 Dify 默认值）
      · 节点「写入问答对txt」 -> process_rule 由 automatic 改为同一套自定义规则
  3. check   —— 只读校验：打印两个节点的 body、检索节点的检索设置、图完整性
  4. rollback <快照名> —— 用备份文件还原草稿
  5. publish —— 把当前草稿发布成一个新版本（应用才真正生效）

用法：
  python wf30_patch.py check
  python wf30_patch.py backup
  python wf30_patch.py apply --publish
  python wf30_patch.py rollback wf30_20261011-100000_before_patch
"""
import datetime as dt
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(HERE, "backup")

APP_ID = "37d7e389-1265-456d-a102-94ecaa2f242a"
APP_NAME = "问答3.0"
PG_CONTAINER = "docker-db_postgres-1"
DB_NAME = "dify"
DB_USER = "postgres"

KB_CREATE_NODE = "node_http_create_kb"   # 新建知识库
KB_WRITE_NODE = "node_http_add_doc"      # 写入问答对txt
RETRIEVAL_NODE = "1788759983275"         # 知识检索

# ---- 用户要求的自定义分段规格 -------------------------------------------------
SEGMENTATION = {
    "separator": "\n\n",     # 分段标识符（写入 JSON 后为字面 \n\n，Dify 会解析成真换行）
    "max_tokens": 1024,      # 分段最大长度 1024 characters
    "chunk_overlap": 50,     # 分段重叠长度 50 characters
}
PRE_PROCESSING_RULES = [
    {"id": "remove_extra_spaces", "enabled": True},   # 替换掉连续的空格、换行符和制表符
    {"id": "remove_urls_emails", "enabled": False},
]
PROCESS_RULE = {
    "mode": "custom",
    "rules": {
        "pre_processing_rules": PRE_PROCESSING_RULES,
        "segmentation": SEGMENTATION,
    },
}

# 用户要求的检索设置：向量检索 + Rerank(bge-reranker-v2-m3) + TopK 3 + 阈值 0.5
EMBEDDING_PROVIDER = "langgenius/siliconflow/siliconflow"
EMBEDDING_MODEL = "Pro/BAAI/bge-m3"
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
RETRIEVAL_MODEL = {
    "search_method": "semantic_search",          # 向量检索
    "reranking_enable": True,
    "reranking_mode": "reranking_model",
    "reranking_model": {
        "reranking_provider_name": EMBEDDING_PROVIDER,
        "reranking_model_name": RERANK_MODEL,
    },
    "top_k": 3,
    "score_threshold_enabled": True,
    "score_threshold": 0.5,
}


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
    row = {
        "graph": _col("graph"),
        "features": _col("features"),
    }
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = "".join(c for c in label if c.isalnum() or c in "-_") or "snap"
    name = "wf30_%s_%s" % (ts, safe)
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


def set_http_json_body(node, obj):
    """把 http 节点的 json body（data[0].value）整体替换成 obj 序列化后的文本。"""
    body = node["data"]["body"]
    assert body.get("type") == "json", "该节点的 body 不是 json 类型"
    body["data"][0]["value"] = json.dumps(obj, ensure_ascii=False)


def build_create_kb_payload():
    """新建知识库：即用型（普通）知识库 + 高质量索引 + bge-m3 + 自定义分段规则 + 检索设置。"""
    return {
        "name": "{{#node_code_filesrc.filename_esc#}}",
        "indexing_technique": "high_quality",
        "permission": "only_me",
        "provider": "vendor",
        "embedding_model": EMBEDDING_MODEL,
        "embedding_model_provider": EMBEDDING_PROVIDER,
        "process_rule": PROCESS_RULE,
        "retrieval_model": RETRIEVAL_MODEL,
    }


def build_add_doc_payload():
    """写入问答对文本：沿用高质量索引，并显式指定同一套分段规则。"""
    return {
        "name": "{{#node_code_filesrc.filename_esc#}}",
        "text": "{{#node_code_decide_id.text_esc#}}",
        "indexing_technique": "high_quality",
        "process_rule": PROCESS_RULE,
    }


def collect_node_ids(graph):
    return {n["id"] for n in graph["nodes"]}


def validate_refs(graph):
    """校验图里不存在指向"不存在的节点 id"的变量引用。"""
    ids = collect_node_ids(graph)
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
            # context / sys / conversation / env 是 Dify 内置变量，不属于节点
            if nid and nid not in ids and nid not in (
                    "sys", "conversation", "env", "context"):
                bad.append((n["id"], n["data"].get("title"), ref))
            i = b + 3
    return bad


def backup_graph_file(graph, name):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    path = os.path.join(BACKUP_DIR, name + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=1)
    return path


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
        % (_dollar(version), vnum, _dollar(marked_name, "difymn"),
           _dollar(marked_comment, "difymc"), APP_ID),
        tuples_only=True)
    new_id = wid.strip().splitlines()[-1].strip()
    _psql("UPDATE apps SET workflow_id='%s', updated_at=now() WHERE id='%s';"
          % (new_id, APP_ID))
    return new_id, vnum, version, wf_type


# ------------------------------------------------------------------ 命令
def cmd_check():
    g = get_graph()
    for nid, title in ((KB_CREATE_NODE, "新建知识库"),
                       (KB_WRITE_NODE, "写入问答对txt"),
                       (RETRIEVAL_NODE, "知识检索")):
        n = find_node(g, nid)
        if not n:
            print("!! 找不到节点 %s (%s)" % (nid, title))
            continue
        d = n["data"]
        print("=" * 78)
        print("节点 %s  %s   type=%s" % (nid, d.get("title"), d.get("type")))
        if d.get("type") == "http-request":
            print("  url :", d.get("url"))
            print("  body:", json.dumps(d["body"], ensure_ascii=False, indent=1))
        else:
            print(json.dumps({k: d[k] for k in
                              ("dataset_ids", "multiple_retrieval_config", "retrieval_mode")
                              if k in d}, ensure_ascii=False, indent=1))
    bad = validate_refs(g)
    print("=" * 78)
    print("悬空变量引用：%d 处" % len(bad))
    for it in bad:
        print("   ", it)


def cmd_backup():
    name, path = snapshot("before_patch")
    print("已备份草稿 ->", path)
    return name


def cmd_apply(do_publish=False):
    g = get_graph()

    # 1) 备份
    name, path = snapshot("before_patch")
    print("[1/5] 已备份当前草稿 -> %s" % path)

    # 2) 改动
    n_create = find_node(g, KB_CREATE_NODE)
    n_write = find_node(g, KB_WRITE_NODE)
    if not n_create or not n_write:
        raise SystemExit("找不到写库节点，请先跑 check")

    old_create = n_create["data"]["body"]["data"][0]["value"]
    old_write = n_write["data"]["body"]["data"][0]["value"]

    set_http_json_body(n_create, build_create_kb_payload())
    set_http_json_body(n_write, build_add_doc_payload())
    print("[2/5] 已改写两个节点的分段规则")

    # 3) 校验
    for nid in (KB_CREATE_NODE, KB_WRITE_NODE):
        n = find_node(g, nid)
        txt = n["data"]["body"]["data"][0]["value"]
        parsed = json.loads(txt.replace("{{#", "X").replace("#}}", "Y"))
        assert parsed["process_rule"]["mode"] == "custom", nid
        seg = parsed["process_rule"]["rules"]["segmentation"]
        assert seg["max_tokens"] == 1024 and seg["chunk_overlap"] == 50, nid
    bad = validate_refs(g)
    if bad:
        raise SystemExit("图校验失败，存在悬空引用：%s" % bad)
    print("[3/5] 校验通过：分段规则正确、图内无悬空引用")

    # 4) 写回草稿
    write_draft(g)
    print("[4/5] 已写回 Dify 草稿")

    # 5) 发布
    if do_publish:
        wid, vnum, ver, wf_type = publish("kb-config", "写入知识库分段/检索配置规范化")
        print("[5/5] 已发布 version#%d（%s，type=%s）" % (vnum, wid[:8], wf_type))
    else:
        print("[5/5] 未发布（加 --publish 才生效到应用运行时）")

    print("\n改动前 新建知识库 body:\n", old_create)
    print("\n改动前 写入问答对 body:\n", old_write)
    return name


def cmd_rollback(name):
    path = os.path.join(BACKUP_DIR, name + ".json")
    if not os.path.exists(path):
        raise SystemExit("找不到快照 %s" % path)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    snapshot("before_rollback")
    write_draft(data["graph"])
    print("已回退草稿到 %s" % name)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd = args[0]
    if cmd == "check":
        cmd_check()
    elif cmd == "backup":
        cmd_backup()
    elif cmd == "apply":
        cmd_apply("--publish" in args)
    elif cmd == "rollback":
        if len(args) < 2:
            raise SystemExit("用法: python wf30_patch.py rollback <快照名>")
        cmd_rollback(args[1])
    elif cmd == "publish":
        wid, vnum, ver, wf_type = publish("manual", "")
        print("已发布 version#%d（%s）" % (vnum, wid[:8]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
