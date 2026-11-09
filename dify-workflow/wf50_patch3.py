#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""「问答5.0」第三阶段：把「新建知识库 / 写入文档」的入库规格对齐用户要求。

规格（用户原文）：
  分段重叠长度            50 characters
  文本预处理规则          替换掉连续的空格、换行符和制表符（remove_extra_spaces=true）
  索引方式                高质量（high_quality）
  Embedding 模型          BAAI/bge-m3
  检索                    向量检索 semantic_search + Rerank BAAI/bge-reranker-v2-m3
                          Top K 3、Score 阈值 0.5

落点：
  1. node_http_add_doc（写入问答对txt）的 process_rule
       chunk_overlap 0 -> 50；remove_extra_spaces false -> true
       （separator "\\n" / max_tokens 4000 保持不动）
  2. node_http_create_kb（确保知识库存在）的 body
       embedding_model Pro/BAAI/bge-m3 -> BAAI/bge-m3
       新增 process_rule（表达意图，建库接口会忽略）
       新增 retrieval_model（这个会真正落盘到 dataset 上）
  3. 知识检索节点 1788759983275 的 vector_setting.embedding_model_name 同步为 BAAI/bge-m3
     （Top K 3 / 阈值 0.5 / Rerank 本来就是对的，只做断言）

⚠️ 关于 chunk_overlap=50 与「一行一段」是否冲突 —— 已源码级确认**不冲突**：
   FixedRecursiveCharacterTextSplitter.split_text 先按 fixed_separator("\n") 切；
   只有当某个切片的长度 > chunk_size(4000) 时才进入 recursive_split_text，
   也才可能用到 chunk_overlap（_merge_splits）。我们每行是一条 200~400 字符的 JSON，
   永远走不到那条路 —— 所以重叠值对本格式不产生任何影响，两者可同时满足。

⚠️ 关于 remove_extra_spaces 与「一行一段」是否冲突 —— 也确认**不冲突**：
   CleanProcessor 的实现是 `\n{3,}` -> `\n\n`、`[空格/制表符等]{2,}` -> 单个空格；
   单个 `\n` 与 JSON 里的单空格都不动，所以行结构完好。

命令（全部可回退）：
  python wf50_patch3.py check
  python wf50_patch3.py apply [--publish]
  python wf50_patch3.py rollback <快照名>
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wf50_patch2 as base  # 复用 psql / get_graph / write_draft / publish 等

BACKUP_DIR = base.BACKUP_DIR
APP_ID = base.APP_ID
APP_NAME = base.APP_NAME

N_CREATE = "node_http_create_kb"     # 确保知识库存在
N_ADD_DOC = "node_http_add_doc"      # 写入问答对txt
N_RETRIEVAL = "1788759983275"        # 知识检索

EMBED_MODEL = "BAAI/bge-m3"
EMBED_PROVIDER = "langgenius/siliconflow/siliconflow"
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
TOP_K = 3
SCORE_THRESHOLD = 0.5

# ---- 目标分段规格 -------------------------------------------------------------
SEGMENTATION = {
    "separator": "\n",      # 分段标识符 = 换行（一行一个 JSON 语句）
    "max_tokens": 4000,     # 分段最大长度
    "chunk_overlap": 50,    # 分段重叠长度 50 characters（用户要求）
}
PRE_PROCESSING_RULES = [
    {"id": "remove_extra_spaces", "enabled": True},   # 替换连续空格/换行/制表符
    {"id": "remove_urls_emails", "enabled": False},
]
PROCESS_RULE = {
    "mode": "custom",
    "rules": {
        "pre_processing_rules": PRE_PROCESSING_RULES,
        "segmentation": SEGMENTATION,
    },
}

# ---- 目标检索规格（建库时的 retrieval_model，会落盘到 dataset）------------------
RETRIEVAL_MODEL = {
    "search_method": "semantic_search",     # 向量检索
    "reranking_enable": True,
    "reranking_model": {
        "reranking_provider_name": EMBED_PROVIDER,
        "reranking_model_name": RERANK_MODEL,
    },
    "reranking_mode": "reranking_model",
    "top_k": TOP_K,
    "score_threshold_enabled": True,
    "score_threshold": SCORE_THRESHOLD,
    "weights": {
        "weight_type": "customized",
        "vector_setting": {
            "vector_weight": 1,
            "embedding_provider_name": EMBED_PROVIDER,
            "embedding_model_name": EMBED_MODEL,
        },
        "keyword_setting": {"keyword_weight": 0},
    },
}


def build_create_body():
    return {
        "name": "{{#node_code_filesrc.filename_esc#}}",
        "indexing_technique": "high_quality",
        "permission": "only_me",
        "provider": "vendor",
        "embedding_model": EMBED_MODEL,
        "embedding_model_provider": EMBED_PROVIDER,
        "process_rule": PROCESS_RULE,
        "retrieval_model": RETRIEVAL_MODEL,
    }


def build_add_doc_body():
    return {
        "name": "{{#node_code_filesrc.filename_esc#}}",
        "text": "{{#node_code_decide_id.text_esc#}}",
        "indexing_technique": "high_quality",
        "process_rule": PROCESS_RULE,
    }


def parse_body(node):
    """把 body 里的模板占位符替换成普通标识符后解析成 dict。"""
    txt = node["data"]["body"]["data"][0]["value"]
    return json.loads(txt.replace("{{#", "X").replace("#}}", "Y"))


# ------------------------------------------------------------------ 快照
def snapshot(label):
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = "".join(c for c in label if c.isalnum() or c in "-_") or "snap"
    name = "wf50c_%s_%s" % (ts, safe)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    path = os.path.join(BACKUP_DIR, name + ".json")
    feats = base._col("features")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"name": name, "at": ts, "app_id": APP_ID, "app_name": APP_NAME,
                   "graph": json.loads(base._col("graph")),
                   "features": json.loads(feats) if feats else {},
                   "workflow_id": base._psql(
                       "SELECT workflow_id FROM apps WHERE id='%s';" % APP_ID,
                       tuples_only=True).strip()},
                  f, ensure_ascii=False, indent=1)
    return name, path


# ------------------------------------------------------------------ 改动
def apply_spec(graph):
    create = base.find_node(graph, N_CREATE)
    add = base.find_node(graph, N_ADD_DOC)
    ret = base.find_node(graph, N_RETRIEVAL)
    for n, t in ((create, N_CREATE), (add, N_ADD_DOC), (ret, N_RETRIEVAL)):
        if not n:
            raise SystemExit("找不到节点 %s (%s)" % (t, t))

    old_add = parse_body(add)["process_rule"]
    old_emb = parse_body(create).get("embedding_model")

    create["data"]["body"]["data"][0]["value"] = json.dumps(build_create_body(), ensure_ascii=False)
    add["data"]["body"]["data"][0]["value"] = json.dumps(build_add_doc_body(), ensure_ascii=False)
    create["data"]["desc"] = ("按文件名建库：高质量索引 + BAAI/bge-m3；"
                              "同名的库会返回 409，由 error_strategy 落默认值后按列表复用")
    add["data"]["desc"] = ("写入问答对：自定义分段 换行/4000/重叠50，预处理替换连续空格换行制表符 "
                           "-> 一行一个 JSON 即一段")

    # 检索节点：同步 embedding 名称（TopK/阈值/Rerank 原本就对）
    cfg = ret["data"].setdefault("multiple_retrieval_config", {})
    vs = cfg.setdefault("weights", {}).setdefault("vector_setting", {})
    vs["embedding_model_name"] = EMBED_MODEL
    vs.setdefault("embedding_provider_name", EMBED_PROVIDER)
    vs["vector_weight"] = 1
    cfg.setdefault("weights", {}).setdefault("keyword_setting", {})["keyword_weight"] = 0
    cfg["weights"]["weight_type"] = "customized"
    return old_add, old_emb


# ------------------------------------------------------------------ 校验
def verify_spec(graph):
    issues = []
    add = base.find_node(graph, N_ADD_DOC)
    create = base.find_node(graph, N_CREATE)
    ret = base.find_node(graph, N_RETRIEVAL)
    if not add or not create or not ret:
        return False, ["缺少 写入问答对txt / 确保知识库存在 / 知识检索 节点"]

    # 1) 写入文档的分段规则
    try:
        pr = parse_body(add)["process_rule"]
        if pr["mode"] != "custom":
            issues.append("写入文档: process_rule.mode=%s（应为 custom）" % pr["mode"])
        seg = pr["rules"]["segmentation"]
        if seg.get("separator") != "\n":
            issues.append("写入文档: separator=%r（应为换行）" % seg.get("separator"))
        if seg.get("max_tokens") != 4000:
            issues.append("写入文档: max_tokens=%r（应为 4000）" % seg.get("max_tokens"))
        if seg.get("chunk_overlap") != 50:
            issues.append("写入文档: chunk_overlap=%r（应为 50）" % seg.get("chunk_overlap"))
        rules = {r["id"]: r["enabled"] for r in pr["rules"]["pre_processing_rules"]}
        if rules.get("remove_extra_spaces") is not True:
            issues.append("写入文档: remove_extra_spaces=%r（应为 True）" % rules.get("remove_extra_spaces"))
        if rules.get("remove_urls_emails") is not False:
            issues.append("写入文档: remove_urls_emails=%r（应为 False）" % rules.get("remove_urls_emails"))
    except Exception as e:
        issues.append("写入文档: body 解析失败 %s" % e)

    # 2) 建库 body
    try:
        b = parse_body(create)
        if b.get("indexing_technique") != "high_quality":
            issues.append("建库: indexing_technique=%r（应为 high_quality）" % b.get("indexing_technique"))
        if b.get("embedding_model") != EMBED_MODEL:
            issues.append("建库: embedding_model=%r（应为 %s）" % (b.get("embedding_model"), EMBED_MODEL))
        if b.get("embedding_model_provider") != EMBED_PROVIDER:
            issues.append("建库: embedding_model_provider=%r（应为 %s）"
                          % (b.get("embedding_model_provider"), EMBED_PROVIDER))
        rm = b.get("retrieval_model") or {}
        if rm.get("search_method") != "semantic_search":
            issues.append("建库: retrieval_model.search_method=%r（应为 semantic_search）" % rm.get("search_method"))
        if rm.get("reranking_enable") is not True:
            issues.append("建库: reranking_enable=%r（应为 True）" % rm.get("reranking_enable"))
        if (rm.get("reranking_model") or {}).get("reranking_model_name") != RERANK_MODEL:
            issues.append("建库: reranking_model=%s（应为 %s）"
                          % (rm.get("reranking_model"), RERANK_MODEL))
        if rm.get("top_k") != TOP_K:
            issues.append("建库: top_k=%r（应为 %d）" % (rm.get("top_k"), TOP_K))
        if rm.get("score_threshold_enabled") is not True or rm.get("score_threshold") != SCORE_THRESHOLD:
            issues.append("建库: 阈值=%s/%s（应为 True/%s）"
                          % (rm.get("score_threshold_enabled"), rm.get("score_threshold"), SCORE_THRESHOLD))
    except Exception as e:
        issues.append("建库: body 解析失败 %s" % e)

    # 3) 检索节点
    c = ret["data"].get("multiple_retrieval_config") or {}
    if c.get("reranking_mode") != "reranking_model":
        issues.append("检索节点: reranking_mode=%r（应为 reranking_model）" % c.get("reranking_mode"))
    if c.get("reranking_enable") is not True:
        issues.append("检索节点: reranking_enable=%r（应为 True）" % c.get("reranking_enable"))
    if (c.get("reranking_model") or {}).get("model") != RERANK_MODEL:
        issues.append("检索节点: reranking_model=%s（应为 %s）" % (c.get("reranking_model"), RERANK_MODEL))
    if c.get("top_k") != TOP_K:
        issues.append("检索节点: top_k=%r（应为 %d）" % (c.get("top_k"), TOP_K))
    if c.get("score_threshold") != SCORE_THRESHOLD:
        issues.append("检索节点: score_threshold=%r（应为 %s）" % (c.get("score_threshold"), SCORE_THRESHOLD))
    vs = ((c.get("weights") or {}).get("vector_setting") or {})
    if vs.get("embedding_model_name") != EMBED_MODEL:
        issues.append("检索节点: vector_setting.embedding_model_name=%r（应为 %s）"
                      % (vs.get("embedding_model_name"), EMBED_MODEL))
    if vs.get("vector_weight") != 1 or ((c.get("weights") or {}).get("keyword_setting") or {}).get("keyword_weight") != 0:
        issues.append("检索节点: 权重应为 向量1 / 关键词0")

    issues += ["悬空引用: %s" % it for it in base.validate_refs(graph)]
    return (not issues), issues


# ------------------------------------------------------------------ 命令
def cmd_check():
    g = base.get_graph()
    print("应用：%s（%s）" % (APP_NAME, APP_ID))
    for nid, label in ((N_CREATE, "确保知识库存在"), (N_ADD_DOC, "写入问答对txt")):
        n = base.find_node(g, nid)
        print("=" * 78)
        print("%s (%s)" % (label, nid))
        print("  body:", json.dumps(parse_body(n), ensure_ascii=False)[:900])
    n = base.find_node(g, N_RETRIEVAL)
    print("=" * 78)
    print("知识检索 (%s)" % N_RETRIEVAL)
    print(" ", json.dumps(n["data"].get("multiple_retrieval_config"), ensure_ascii=False))
    ok, issues = verify_spec(g)
    print("=" * 78)
    print("规格自检：%s" % ("✅ 通过" if ok else "❌ 未达标"))
    for it in issues:
        print("   -", it)


def cmd_verify():
    g = base.get_graph()
    ok, issues = verify_spec(g)
    print("草稿规格自检：%s" % ("✅ 通过" if ok else "❌ 不通过"))
    for it in issues:
        print("   -", it)
    raise SystemExit(0 if ok else 1)


def cmd_apply(do_publish=False):
    g = base.get_graph()
    name, path = snapshot("before_spec")
    print("[1/5] 已备份当前草稿 -> %s" % path)

    old_add, old_emb = apply_spec(g)
    print("[2/5] 已改写入库规格：分段重叠 0 -> 50、开启 remove_extra_spaces、"
          "embedding %s -> %s" % (old_emb, EMBED_MODEL))
    print("      改动前 写入文档 process_rule:\n        ", json.dumps(old_add, ensure_ascii=False))

    bad = base.validate_refs(g)
    if bad:
        raise SystemExit("图校验失败，悬空引用：%s" % bad)
    ok, issues = verify_spec(g)
    print("[3/5] 规格自检 %s" % ("✅ 通过" if ok else "❌ 不通过"))
    for it in issues:
        print("      -", it)
    if not ok:
        raise SystemExit("规格自检不通过，未写库。")

    base.write_draft(g)
    print("[4/5] 已写回 Dify 草稿")

    if do_publish:
        wid, vnum, ver, wf_type = base.publish("kb-ingest-spec",
                                               "入库规格：重叠50+预处理；bge-m3；向量检索+rerank TopK3 阈值0.5")
        print("[5/5] 已发布 version#%d（%s，type=%s）" % (vnum, wid[:8], wf_type))
    else:
        print("[5/5] 未发布（加 --publish 才生效到运行时）")
    print("回退：python wf50_patch3.py rollback %s" % name)
    return name


def cmd_rollback(name):
    path = os.path.join(BACKUP_DIR, name + ".json")
    if not os.path.exists(path):
        raise SystemExit("找不到快照 %s" % path)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    snapshot("before_rollback")
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
    elif cmd == "verify":
        cmd_verify()
    elif cmd == "apply":
        cmd_apply("--publish" in args)
    elif cmd == "rollback":
        if len(args) < 2:
            raise SystemExit("用法: python wf50_patch3.py rollback <快照名>")
        cmd_rollback(args[1])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
