#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""「归档到检索库」端到端实测（走本机前端接口 + Dify Service API，全程可回滚）。

验证链路（= 用户要的效果）：
  对话里上传文件 → 工作流把问答对写进一个「新知识库」
  → 在「知识库管理」里把它【归档到检索库】
  → 【新建对话】就能检索到

覆盖：
  1. 检索库清单接口（/api/kb/retrieval）
  2. 源知识库/文档在前端列表里可见
  3. 库级归档：分段搬迁条数、目标库出现同名文档、目标库 /retrieve 命中
  4. ★ 新建对话实测：调 /v1/chat-messages（conversation_id 为空 = 全新会话）
     断言回答/引用里出现只有归档内容才有的关键词
  5. 反向用例：目标不是检索库 → 400；目标 == 源 → 400；缺 target → 400
  6. 文档级归档接口
  7. deleteSource=true → 源文档被删除
  8. 清理：删除归档进目标库的文档、删除临时库，检索库清单保持原样

用法：
    python verify_archive.py                 # 完整跑（含新建对话实测，约 4~6 分钟）
    MQ50_SKIP_CHAT=1 python verify_archive.py  # 跳过对话实测（约 1 分钟）

零第三方依赖，只用标准库。需要本机服务已启动、Dify 可用。
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_config():
    for name in ("config.json", "config.example.json"):
        p = os.path.join(_HERE, name)
        if os.path.isfile(p):
            try:
                return json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
    return {}


CFG = _load_config()
PORT = os.environ.get("MQ50_PORT") or CFG.get("server", {}).get("port", 8001)
BASE = os.environ.get("MQ50_BASE") or ("http://127.0.0.1:%s" % PORT)
DIFY = (CFG.get("dify", {}).get("base_url") or "http://127.0.0.1/v1").rstrip("/")
DS_KEY = (CFG.get("dify", {}).get("dataset_api_key") or "").strip()
APP_KEY = (CFG.get("dify", {}).get("api_key") or "").strip()

if not DS_KEY or DS_KEY.startswith("dataset-xxx"):
    print("!! config.json 里没有可用的 dataset_api_key。")
    sys.exit(2)

PASS, FAIL = [], []


def ok(msg):
    PASS.append(msg)
    print("  \u2713 " + msg)


def bad(msg):
    FAIL.append(msg)
    print("  \u2717 " + msg)


def chk(cond, msg, detail=""):
    (ok if cond else bad)(msg + ("" if cond else (("  " + str(detail)) if detail else "")))
    return bool(cond)


def call(url, method="GET", body=None, key=None, timeout=180):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        raw = r.read().decode("utf-8", "ignore")
        return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "ignore")
        except Exception:
            pass
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"message": raw[:300]}


def dify(method, path, body=None, timeout=180):
    return call(DIFY + path, method, body, DS_KEY, timeout)


def api(method, path, body=None, token=None, timeout=600):
    return call(BASE + path, method, body, token, timeout)


def wait_indexed(ds_id, doc_id, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, obj = dify("GET", "/datasets/%s/documents/%s" % (ds_id, doc_id))
        if st == 200 and obj.get("indexing_status") == "completed":
            return True
        if st == 200 and obj.get("indexing_status") == "error":
            return False
        time.sleep(2)
    return False


def retrieve(ds_id, query, timeout=120):
    st, obj = dify("POST", "/datasets/%s/retrieve" % ds_id, {"query": query}, timeout=timeout)
    if st != 200:
        return None, obj
    return (obj.get("records") or []), obj


# ---- 测试用的独特内容（现有资料里绝不可能出现）--------------------------------
MARK = "蓝鳍云杉综合征"
SRC_TEXT = (
    "%s的现场识别要点：患者接触云杉花粉后 6 小时内出现指端发蓝、心悸与口周麻木。\n"
    "应急处置第一步是脱离花粉环境并保持平卧，禁止剧烈运动。\n"
    "若出现喉头水肿征象，立即肌注肾上腺素并转运，转运途中持续吸氧。\n"
    "禁忌：不得使用阿司匹林，会加重指端发绀。\n"
) % MARK
PROBE = "%s的现场识别要点和禁忌是什么" % MARK
SRC_DOC_NAME = "__归档自检__.txt"


def main():
    print("=" * 74)
    print("「归档到检索库」端到端实测")
    print("=" * 74)

    created_ds, archived_doc_in_target = [], []
    token = None

    # ---------- 0. 自检账号 ----------
    uname, passwd = "_verify_kb", "verify_kb_123456"
    st, obj = api("POST", "/api/register", {"username": uname, "password": passwd})
    if st != 200 or not obj.get("token"):
        st, obj = api("POST", "/api/login", {"username": uname, "password": passwd})
    if st != 200 or not obj.get("token"):
        print("!! 无法注册/登录自检账号，请确认服务已启动：", st, obj)
        return 1
    token = obj["token"]
    ok("自检账号就绪（%s）" % uname)

    # ---------- 1. 检索库清单 ----------
    print("\n【1】检索库清单接口")
    st, obj = api("GET", "/api/kb/retrieval", token=token)
    chk(st == 200 and obj.get("ok"), "GET /api/kb/retrieval 返回 200", (st, obj))
    rl = obj.get("retrieval") or {}
    ids = rl.get("ids") or []
    chk(len(ids) >= 1, "检索库清单非空（%d 个）" % len(ids), rl)
    named = [x for x in (rl.get("datasets") or []) if x.get("exists") and x.get("name")]
    chk(len(named) == len(ids), "清单里每个库都能解析出名称（%d/%d）" % (len(named), len(ids)),
        [(x.get("id"), x.get("exists"), x.get("error")) for x in (rl.get("datasets") or [])])
    target = os.environ.get("MQ50_ARCH_TARGET") or (ids[0] if ids else "")
    target_name = ""
    for x in (rl.get("datasets") or []):
        if x.get("id") == target:
            target_name = x.get("name") or ""
    chk(bool(target), "选出归档目标检索库：%s" % (target_name or target))

    # ---------- 2. 造源数据 ----------
    print("\n【2】建临时源知识库（模拟「上传文件后新建的知识库」）")
    src_name = "__归档自检源__%d.tmp" % int(time.time() % 100000)
    st, obj = dify("POST", "/datasets", {
        "name": src_name, "permission": "only_me", "indexing_technique": "high_quality",
        "provider": "vendor",
        "embedding_model": "BAAI/bge-m3",
        "embedding_model_provider": "langgenius/siliconflow/siliconflow",
    })
    if st != 200 or not obj.get("id"):
        bad("建临时源知识库失败：%s %s" % (st, obj))
        return finish(created_ds, archived_doc_in_target, token, target)
    src_ds = obj["id"]
    created_ds.append(src_ds)
    ok("源知识库已建：%s（%s）" % (src_name, src_ds))

    st, obj = dify("POST", "/datasets/%s/document/create-by-text" % src_ds, {
        "name": SRC_DOC_NAME,
        "text": SRC_TEXT,
        "indexing_technique": "high_quality",
        "process_rule": {
            "mode": "custom",
            "rules": {
                "pre_processing_rules": [
                    {"id": "remove_extra_spaces", "enabled": True},
                    {"id": "remove_urls_emails", "enabled": False},
                ],
                "segmentation": {"separator": "\n", "max_tokens": 4000, "chunk_overlap": 50},
            },
        },
    })
    src_doc = ((obj or {}).get("document") or {}).get("id")
    chk(st == 200 and src_doc, "源文档已写入（4 行）", (st, obj))
    if not src_doc:
        return finish(created_ds, archived_doc_in_target, token, target)
    wait_indexed(src_ds, src_doc)

    st, obj = api("GET", "/api/kb?keyword=" + urllib.parse.quote(SRC_DOC_NAME[:4]), token=token)
    hit = [x for x in (obj.get("datasets") or []) if x.get("id") == src_ds]
    chk(bool(hit), "源知识库在「知识库管理」列表里可见")

    st, obj = api("GET", "/api/kb/%s/documents" % src_ds, token=token)
    docs = obj.get("documents") or []
    seg_total = None
    if docs:
        st2, obj2 = api("GET", "/api/kb/%s/documents/%s/segments" % (src_ds, src_doc), token=token)
        seg_total = obj2.get("total")
    chk(bool(docs), "源文档在前端可见")
    print("      源文档分段数 = %s（应为 4：一行一段）" % seg_total)

    # ---------- 3. 反向用例 ----------
    print("\n【3】反向用例（不该归档的情况必须被拦住）")
    st, obj = api("POST", "/api/kb/%s/archive-to" % src_ds, {"target": src_ds}, token=token)
    chk(st == 400, "目标 == 源 → 400", (st, obj))
    st, obj = api("POST", "/api/kb/%s/archive-to" % src_ds, {"target": "not-a-dataset"}, token=token)
    chk(st == 400 and "检索库" in str(obj.get("error", "")),
        "目标不是检索库 → 400 并给出原因", (st, obj))
    st, obj = api("POST", "/api/kb/%s/archive-to" % src_ds, {}, token=token)
    chk(st == 400, "缺 target → 400", (st, obj))

    # ---------- 4. 库级归档 ----------
    print("\n【4】库级归档：%s → %s" % (src_name, target_name or target))
    t0 = time.time()
    st, obj = api("POST", "/api/kb/%s/archive-to" % src_ds,
                  {"target": target, "deleteSource": False}, token=token, timeout=900)
    dt = time.time() - t0
    chk(st == 200 and obj.get("ok"), "归档接口返回 200（%.1fs）" % dt, (st, obj))
    arc = (obj or {}).get("archived") or []
    skp = (obj or {}).get("skipped") or []
    chk(len(arc) == 1 and not skp, "归档成功 1 篇、无跳过", (arc, skp))
    if arc:
        a = arc[0]
        print("      目标文档 id=%s  段数=%s  字数=%s  索引=%s"
              % (a.get("targetDocumentId"), a.get("segments"), a.get("chars"), a.get("indexingStatus")))
        chk(a.get("segments") == 4, "搬迁了 4 段（一行一段）", a)
        chk(a.get("indexingStatus") == "completed", "目标文档索引已完成", a)
        if a.get("targetDocumentId"):
            archived_doc_in_target.append(a["targetDocumentId"])

    # 目标库里出现同名文档
    st, obj = api("GET", "/api/kb/%s/documents?keyword=%s" % (target, urllib.parse.quote(SRC_DOC_NAME)),
                  token=token)
    tdocs = [d for d in (obj.get("documents") or []) if d.get("name") == SRC_DOC_NAME]
    chk(bool(tdocs), "目标检索库里能看到归档进来的文档《%s》" % SRC_DOC_NAME, obj)
    if tdocs and tdocs[0].get("id") not in archived_doc_in_target:
        archived_doc_in_target.append(tdocs[0]["id"])
    if tdocs:
        st2, obj2 = api("GET", "/api/kb/%s/documents/%s/segments" % (target, tdocs[0]["id"]), token=token)
        chk(obj2.get("total") == 4, "目标文档分段数 = 4", obj2.get("total"))

    # 目标库能召回
    t1 = time.time()
    got = False
    while time.time() - t1 < 90:
        recs, raw = retrieve(target, PROBE)
        if recs:
            got = any(MARK in (r.get("segment") or {}).get("content", "") for r in recs)
            if got:
                break
        time.sleep(3)
    chk(got, "目标检索库 /retrieve 能召回归档内容（关键词：%s）" % MARK)

    # ---------- 5. 新建对话实测 ----------
    if os.environ.get("MQ50_SKIP_CHAT"):
        print("\n【5】新建对话实测  —— 已按 MQ50_SKIP_CHAT 跳过")
    else:
        print("\n【5】★ 新建对话实测（conversation_id 为空 = 全新会话，约 3~5 分钟）")
        ans, srcs = chat_new(PROBE)
        print("      回答节选：%s" % (ans[:160].replace("\n", " ") if ans else "(空)"))
        chk(bool(ans), "新对话拿到了回答", ans[:200])
        hit_ans = (MARK in (ans or "")) or ("云杉" in (ans or "")) or ("肾上腺素" in (ans or ""))
        hit_src = any(MARK in s for s in srcs)
        chk(hit_ans or hit_src, "新对话的回答/引用命中了归档内容（回答命中=%s 引用命中=%s）"
            % (hit_ans, hit_src), (ans[:300], srcs[:2]))

    # ---------- 6. 文档级归档 + deleteSource ----------
    print("\n【6】文档级归档接口 + deleteSource")
    src2_name = "__归档自检源2__%d.tmp" % int(time.time() % 100000)
    st, obj = dify("POST", "/datasets", {
        "name": src2_name, "permission": "only_me", "indexing_technique": "high_quality",
        "provider": "vendor",
        "embedding_model": "BAAI/bge-m3",
        "embedding_model_provider": "langgenius/siliconflow/siliconflow",
    })
    if st == 200 and obj.get("id"):
        src2 = obj["id"]
        created_ds.append(src2)
        st, obj = dify("POST", "/datasets/%s/document/create-by-text" % src2, {
            "name": SRC_DOC_NAME, "text": SRC_TEXT, "indexing_technique": "high_quality",
            "process_rule": {
                "mode": "custom",
                "rules": {
                    "pre_processing_rules": [
                        {"id": "remove_extra_spaces", "enabled": True},
                        {"id": "remove_urls_emails", "enabled": False},
                    ],
                    "segmentation": {"separator": "\n", "max_tokens": 4000, "chunk_overlap": 50},
                },
            },
        })
        doc2 = ((obj or {}).get("document") or {}).get("id")
        chk(wait_indexed(src2, doc2), "第二个源文档索引完成", doc2)
        st, obj = api("POST", "/api/kb/%s/documents/%s/archive-to" % (src2, doc2),
                      {"target": target, "deleteSource": True}, token=token, timeout=900)
        chk(st == 200 and len(obj.get("archived") or []) == 1, "文档级归档返回 200", (st, obj))
        chk(obj.get("deletedSource") is True, "回执里 deletedSource=true")
        st, obj2 = api("GET", "/api/kb/%s/documents" % src2, token=token)
        chk((obj2.get("total") or 0) == 0, "源文档已被删除（源库剩 0 篇）", obj2.get("total"))
        for a in (obj.get("archived") or []):
            if a.get("targetDocumentId"):
                archived_doc_in_target.append(a["targetDocumentId"])
    else:
        bad("建第二个临时源库失败：%s %s" % (st, obj))

    # ---------- 7. 清单未被改动 ----------
    print("\n【7】收尾检查")
    st, obj = api("GET", "/api/kb/retrieval", token=token)
    chk((obj.get("retrieval") or {}).get("ids") == ids, "检索库清单在自检前后保持一致")

    return finish(created_ds, archived_doc_in_target, token, target)


def chat_new(query, timeout=900):
    """调 /v1/chat-messages（conversation_id="" → 全新会话），返回 (回答文本, 引用内容列表)。"""
    body = {"inputs": {}, "query": query, "response_mode": "streaming",
            "conversation_id": "", "user": "verify_archive"}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(DIFY + "/chat-messages", data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + APP_KEY})
    answer, srcs = [], []
    t0 = time.time()
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        for raw in r:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                ev = json.loads(payload)
            except Exception:
                continue
            et = ev.get("event")
            if et in ("message", "agent_message"):
                answer.append(ev.get("answer") or "")
            elif et == "message_end":
                meta = ev.get("metadata") or {}
                for r2 in (meta.get("retriever_resources") or []):
                    srcs.append(r2.get("content") or "")
            elif et == "error":
                print("      [对话报错] %s" % ev)
            if time.time() - t0 > timeout:
                break
    except Exception as e:
        print("      [对话请求异常] %s" % e)
    return "".join(answer), srcs


def finish(created_ds, archived_docs, token, target=""):
    """清理：删除归档到目标库的文档、删除所有临时知识库。"""
    print("\n【8】清理")
    for d in archived_docs:
        if not d:
            continue
        st, _ = dify("DELETE", "/datasets/%s/documents/%s" % (target, d))
        print("      删除目标库文档 %s -> %s" % (d, st))
    for ds in created_ds:
        st, _ = dify("DELETE", "/datasets/%s" % ds)
        print("      删除临时知识库 %s -> %s" % (ds, st))

    print("\n" + "=" * 74)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  \u2717 " + f)
    print("=" * 74)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
