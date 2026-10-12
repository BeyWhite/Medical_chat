#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识库管理功能 · 端到端实测（走本机前端接口，全流程可回滚）。

覆盖：
  1. 建临时知识库 + 写入文档（直连 Dify Service API，模拟真实数据）
  2. 列表 / 详情接口
  3. 「禁用」→ 检索确实召回不到（用 /retrieve 验证，比跑一问一答快得多）
  4. 「启用」→ 检索恢复
  5. 编辑知识库（名称 / 描述 / TopK / 阈值）并回读校验
  6. 文档：归档 / 取消归档 / 禁用 / 启用 / 列表状态筛选
  7. 分段：列表 / 编辑内容与关键词（重建索引）/ 删除
  8. 删除文档、删除知识库
  9. 权限校验、清理

用法：
    python verify_kb.py            # 完整跑一遍（需要本机服务已在运行）
环境：先用 start.bat / start.sh 启动服务；Dify 需可用；config.json 里要填好
      dataset_api_key（知识库密钥）。零第三方依赖，只用标准库。
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---- 从 config.json 读取端口与密钥（不写死任何本机信息）----
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

if not DS_KEY or re.match(r"^dataset-x+$", DS_KEY) or DS_KEY.startswith("dataset-xxx"):
    print("!! config.json 里没有可用的 dataset_api_key（知识库密钥）。")
    print("   请先在网页「接口设置」里填写，或直接把密钥写进 config.json 的 dify.dataset_api_key。")
    sys.exit(2)

PASS, FAIL = [], []


def ok(msg):
    PASS.append(msg)
    print("  \u2713 " + msg)


def bad(msg):
    FAIL.append(msg)
    print("  \u2717 " + msg)


def chk(cond, msg, detail=""):
    (ok if cond else bad)(msg + ("" if cond else ("  " + str(detail) if detail else "")))
    return cond


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


def api(method, path, body=None, token=None, timeout=180):
    """走本机前端接口。"""
    return call(BASE + path, method, body, token, timeout)


def retrieve(ds_id, query):
    """直接问 Dify 这个库能不能召回（禁用后应为 0 条）。"""
    st, obj = dify("POST", "/datasets/%s/retrieve" % ds_id, {"query": query}, timeout=120)
    if st != 200:
        return None, obj
    return (obj.get("records") or []), obj


def wait_indexed(ds_id, doc_id, timeout=180):
    """等文档索引完成。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, obj = dify("GET", "/datasets/%s/documents/%s" % (ds_id, doc_id))
        if st == 200 and obj.get("indexing_status") == "completed":
            return True
        if st == 200 and obj.get("indexing_status") == "error":
            return False
        time.sleep(2)
    return False


IDLE_STATUS = ("available", "disabled", "archived", "error")


def wait_doc_idle(ds_id, doc_id, timeout=180):
    """等文档状态操作「空闲」——Dify 在重新索引期间会拒绝状态变更。

    实测：把文档 enable 回去后会触发重新索引，大约 3 秒内再改状态会被拒。
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, obj = dify("GET", "/datasets/%s/documents/%s" % (ds_id, doc_id))
        if st == 200 and obj.get("display_status") in IDLE_STATUS:
            time.sleep(1.0)   # 再多给 1 秒余量
            return True
        time.sleep(1.5)
    return False


def status_patch(path, body, token, tries=10, gap=3.0):
    """改文档/分段状态：撞上「is being indexed」就退避重试。

    Dify 在重新索引期间会拒绝状态变更（400 Document ... is being indexed）。
    首次跑时偶发（机器忙时索引慢），这里统一兜住，避免自检假红。
    """
    st, obj = None, {}
    for _ in range(tries):
        st, obj = api("PATCH", path, body, token=token)
        if st == 200 or "being indexed" not in str(obj.get("error", "")):
            return st, obj
        time.sleep(gap)
    return st, obj


def retrieve(ds_id, query):
    """直接问 Dify 这个库能不能召回（禁用后应为 0 条）。"""
    st, obj = dify("POST", "/datasets/%s/retrieve" % ds_id, {"query": query}, timeout=120)
    if st != 200:
        return None, obj
    return (obj.get("records") or []), obj


def retrieve_wait(ds_id, query, want_positive, timeout=90, label=""):
    """轮询检索直到命中数符合预期（Dify 改状态后需要几秒才反映到检索上）。"""
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        recs, _ = retrieve(ds_id, query)
        if recs is not None:
            last = len(recs)
            if (last > 0) == want_positive:
                return last, time.time() - t0
        time.sleep(3)
    return last, time.time() - t0


SAMPLE = (
    "急性高原病现场处置要点：进入高原后出现头痛、恶心、乏力，应立即停止上升并原地休息。\n"
    "轻型者可给予吸氧，每分钟 2 至 4 升，同时保暖、补充含糖热饮。\n"
    "若出现意识改变、共济失调或静息状态下呼吸困难，属于重型，必须尽快下降海拔并送医。\n"
    "高压氧舱与地塞米松是院内常用手段，但下降海拔始终是最有效的处置。\n"
)
PROBE = "急性高原病怎么处理"


def main():
    print("=" * 74)
    print("知识库管理 · 端到端实测")
    print("=" * 74)

    # ---------- 0. 准备：用一个固定的自检账号（已存在就直接登录，避免反复跑时堆积账号）----------
    uname = "_verify_kb"
    passwd = "verify_kb_123456"
    st, obj = api("POST", "/api/register", {"username": uname, "password": passwd})
    if st != 200 or not obj.get("token"):
        st, obj = api("POST", "/api/login", {"username": uname, "password": passwd})
    if st != 200 or not obj.get("token"):
        print("!! 无法注册/登录自检账号，请确认服务已启动：", st, obj)
        return 1
    token = obj["token"]
    ok("自检账号就绪（%s）" % uname)

    # ---------- 1. 建临时知识库 + 文档 ----------
    kb_name = "KB管理实测_%d" % int(time.time())
    st, kb = dify("POST", "/datasets", {
        "name": kb_name,
        "description": "临时库，测试完即删",
        "indexing_technique": "high_quality",
        "permission": "only_me",
        "provider": "vendor",
        "embedding_model": "BAAI/bge-m3",
        "embedding_model_provider": "langgenius/siliconflow/siliconflow",
    })
    if st != 200:
        print("!! 建库失败：", st, kb)
        return 1
    ds_id = kb["id"]
    ok("已建临时知识库 %s（%s）" % (kb_name, ds_id[:8]))

    st, doc = dify("POST", "/datasets/%s/document/create-by-text" % ds_id, {
        "name": "高原病处置.txt",
        "text": SAMPLE,
        "indexing_technique": "high_quality",
        "process_rule": {"mode": "custom", "rules": {
            "pre_processing_rules": [{"id": "remove_extra_spaces", "enabled": True},
                                     {"id": "remove_urls_emails", "enabled": False}],
            "segmentation": {"separator": "\n", "max_tokens": 4000, "chunk_overlap": 0},
        }},
    }, timeout=300)
    if st != 200:
        print("!! 写文档失败：", st, doc)
        dify("DELETE", "/datasets/" + ds_id)
        return 1
    doc_id = doc["document"]["id"]
    chk(wait_indexed(ds_id, doc_id), "文档索引完成", doc.get("document", {}).get("indexing_status"))
    wait_doc_idle(ds_id, doc_id)   # indexing_status=completed 后 display_status 可能还是 indexing

    recs, _ = retrieve(ds_id, PROBE)
    chk(recs is not None and len(recs) > 0, "基线：启用状态下能召回到内容（%s 条）" % (len(recs) if recs else 0))

    # ---------- 2. 列表 / 详情 ----------
    st, obj = api("GET", "/api/kb?limit=100", token=token)
    chk(st == 200 and obj.get("ok"), "知识库列表接口可用")
    mine = [d for d in (obj.get("datasets") or []) if d["id"] == ds_id]
    chk(bool(mine), "列表里能找到临时知识库")
    if mine:
        d0 = mine[0]
        chk(d0["state"] == "enabled", "列表状态 = 已启用", d0["state"])
        chk(d0["documentCount"] == 1, "文档数 = 1", d0["documentCount"])
        chk(d0["embeddingModel"] == "BAAI/bge-m3", "向量模型回读正确", d0["embeddingModel"])

    st, obj = api("GET", "/api/kb/" + ds_id, token=token)
    chk(st == 200 and (obj.get("dataset") or {}).get("id") == ds_id, "知识库详情接口可用")

    st, obj = api("GET", "/api/kb/" + ds_id + "/documents", token=token)
    chk(st == 200 and len(obj.get("documents") or []) == 1, "文档列表接口可用")

    st, obj = api("GET", "/api/kb/" + ds_id + "/documents/" + doc_id + "/segments?limit=5", token=token)
    segs = obj.get("segments") or []
    chk(st == 200 and len(segs) > 0, "分段列表接口可用（共 %s 段）" % obj.get("total"))
    seg_id = segs[0]["id"] if segs else ""

    # ---------- 3. 整库「禁用」→ 检索应召不到 ----------
    st, obj = api("POST", "/api/kb/%s/toggle" % ds_id, {"enabled": False}, token)
    chk(st == 200 and obj.get("affected") == 1, "整库禁用：影响 1 个文档", obj)

    n, dt = retrieve_wait(ds_id, PROBE, False, timeout=60)
    chk(n == 0, "★ 禁用后检索命中 0 条（证明禁用真的生效，%.0fs 内达成）" % dt, "实得 %s 条" % n)
    st, obj = api("GET", "/api/kb/" + ds_id, token=token)
    chk((obj.get("dataset") or {}).get("state") == "disabled", "详情状态 = 已禁用", (obj.get("dataset") or {}).get("state"))

    # ---------- 4. 「启用」→ 检索恢复 ----------
    st, obj = api("POST", "/api/kb/%s/toggle" % ds_id, {"enabled": True}, token)
    chk(st == 200 and obj.get("affected") == 1, "整库启用：影响 1 个文档", obj)
    n, dt = retrieve_wait(ds_id, PROBE, True, timeout=90)
    chk(n is not None and n > 0, "★ 启用后检索恢复（%s 条，%.0fs 内恢复）" % (n, dt),
        "实得 %s 条" % n)
    st, obj = api("GET", "/api/kb/" + ds_id, token=token)
    chk((obj.get("dataset") or {}).get("state") == "enabled", "详情状态回到 已启用")
    wait_doc_idle(ds_id, doc_id)

    # ---------- 5. 编辑知识库 ----------
    new_name = kb_name + "_改名"
    st, obj = api("PATCH", "/api/kb/" + ds_id,
                  {"name": new_name, "description": "改过的描述", "retrieval": {"topK": 5, "scoreThresholdEnabled": True, "scoreThreshold": 0.6}}, token)
    chk(st == 200 and obj.get("ok"), "编辑接口返回成功", obj)
    d = (obj.get("dataset") or {})
    chk(d.get("name") == new_name, "名称已改", d.get("name"))
    chk(d.get("description") == "改过的描述", "描述已改", d.get("description"))
    chk((d.get("retrieval") or {}).get("topK") == 5, "TopK 已改（%s）" % (d.get("retrieval") or {}).get("topK"))
    chk((d.get("retrieval") or {}).get("scoreThreshold") == 0.6,
        "阈值已改（%s）" % (d.get("retrieval") or {}).get("scoreThreshold"))
    st, obj = api("GET", "/api/kb/" + ds_id, token=token)
    chk((obj.get("dataset") or {}).get("name") == new_name, "回读确认名称已落盘")
    chk((((obj.get("dataset") or {}).get("retrieval") or {}).get("topK")) == 5, "回读确认 TopK 已落盘")

    st, obj = api("PATCH", "/api/kb/" + ds_id, {"name": "   "}, token=token)
    chk(st == 400, "空名称被拒绝（%s）" % st)

    # 把阈值改回去：0.6 会让下面的"检索能否命中"断言失真
    # （唯一含"急性高原病"的那段正文后面会被改写，剩余分段在 0.6 阈值下会被过滤掉）
    st, obj = api("PATCH", "/api/kb/" + ds_id,
                  {"retrieval": {"topK": 3, "scoreThresholdEnabled": False, "scoreThreshold": 0.5}}, token=token)
    chk(st == 200 and not (obj.get("dataset") or {}).get("retrieval", {}).get("scoreThresholdEnabled"),
        "阈值开关可关闭（改回后检索断言才可靠）", obj)

    # ---------- 6. 文档：归档 / 取消归档 / 禁用 / 启用 ----------
    docpath = "/api/kb/%s/documents/%s" % (ds_id, doc_id)
    wait_doc_idle(ds_id, doc_id)
    st, obj = status_patch(docpath, {"archived": True}, token)
    chk(st == 200 and obj.get("action") == "archive", "归档文档", obj)
    time.sleep(1)
    st, obj = api("GET", "/api/kb/%s/documents?status=archived" % ds_id, token=token)
    chk(len(obj.get("documents") or []) == 1, "「已归档」筛选能看到它", obj.get("total"))
    st, obj = status_patch(docpath, {"archived": False}, token)
    chk(st == 200 and obj.get("action") == "un_archive", "取消归档", obj)
    wait_doc_idle(ds_id, doc_id)

    st, obj = status_patch(docpath, {"enabled": False}, token)
    chk(st == 200 and obj.get("action") == "disable", "禁用单个文档", obj)
    st, obj = api("GET", "/api/kb/%s/documents?status=disabled" % ds_id, token=token)
    chk(len(obj.get("documents") or []) == 1, "「已禁用」筛选能看到它", obj.get("total"))
    st, obj = status_patch(docpath, {"enabled": True}, token)
    chk(st == 200 and obj.get("action") == "enable", "启用单个文档", obj)
    wait_doc_idle(ds_id, doc_id)

    # ---------- 7. 分段：编辑 / 禁用 / 删除 ----------
    if seg_id:
        target = "【已编辑】丙段：高原肺水肿首选硝苯地平，禁用后不应再被召回。"
        st, obj = api("POST", "/api/kb/%s/documents/%s/segments/%s" % (ds_id, doc_id, seg_id),
                      {"content": target, "keywords": ["高原病", "硝苯地平"], "enabled": True}, token=token)
        chk(st == 200 and obj.get("ok"), "编辑分段内容 + 关键词（触发重建索引）", obj)

        hit, ok_content, kw_ok = [], False, False
        t0 = time.time()
        while time.time() - t0 < 120:
            st, obj = api("GET", "/api/kb/%s/documents/%s/segments?limit=100" % (ds_id, doc_id), token=token)
            hit = [s for s in (obj.get("segments") or []) if s["id"] == seg_id]
            if hit and "【已编辑】" in hit[0]["content"] and hit[0].get("status") == "completed":
                ok_content = True
                kw_ok = hit[0].get("keywords") == ["高原病", "硝苯地平"]
                if kw_ok:
                    break
            time.sleep(3)
        chk(ok_content, "分段内容已更新且索引重建完成（%.0fs）" % (time.time() - t0),
            hit[0].get("status") if hit else None)
        chk(kw_ok, "★ 分段关键词已落盘（内容与关键词拆两次提交后才生效）",
            hit[0].get("keywords") if hit else None)

        # 单独改关键词（内容不动）也要能生效
        st, obj = api("POST", "/api/kb/%s/documents/%s/segments/%s" % (ds_id, doc_id, seg_id),
                      {"keywords": ["高原病", "硝苯地平", "肺水肿"]}, token=token)
        chk(st == 200, "只改关键词（不传内容）返回成功", obj)
        kw2 = False
        t0 = time.time()
        while time.time() - t0 < 45:
            st, obj = api("GET", "/api/kb/%s/documents/%s/segments?limit=100" % (ds_id, doc_id), token=token)
            hit = [s for s in (obj.get("segments") or []) if s["id"] == seg_id]
            if hit and (hit[0].get("keywords") or []) == ["高原病", "硝苯地平", "肺水肿"]:
                kw2 = True
                break
            time.sleep(3)
        chk(kw2, "★ 单独改关键词也能落盘", hit[0].get("keywords") if hit else None)

        # 把该文档**所有**分段都禁用 → 整篇应召不到（避免"别的段也含关键词"造成误判）
        st, obj = api("GET", "/api/kb/%s/documents/%s/segments?limit=100" % (ds_id, doc_id), token=token)
        all_seg_ids = [s["id"] for s in (obj.get("segments") or [])]
        bad = 0
        for sid in all_seg_ids:
            st, o2 = api("POST", "/api/kb/%s/documents/%s/segments/%s" % (ds_id, doc_id, sid),
                         {"enabled": False}, token=token)
            if st != 200:
                bad += 1
        chk(bad == 0, "禁用全部 %d 个分段（失败 %d 个）" % (len(all_seg_ids), bad))
        n, dt = retrieve_wait(ds_id, PROBE, False, timeout=60)
        chk(n == 0, "★ 全部分段禁用后检索命中 0 条（%.0fs 内达成）" % dt, "实得 %s 条" % n)

        bad = 0
        for sid in all_seg_ids:
            st, o2 = api("POST", "/api/kb/%s/documents/%s/segments/%s" % (ds_id, doc_id, sid),
                         {"enabled": True}, token=token)
            if st != 200:
                bad += 1
        chk(bad == 0, "启用全部 %d 个分段（失败 %d 个）" % (len(all_seg_ids), bad))
        n, dt = retrieve_wait(ds_id, PROBE, True, timeout=120)
        chk(n is not None and n > 0, "★ 全部分段启用后检索恢复（%s 条，%.0fs 内恢复）" % (n, dt), "实得 %s 条" % n)

        st, obj = api("GET", "/api/kb/%s/documents/%s/segments?limit=100" % (ds_id, doc_id), token=token)
        before = obj.get("total") or 0
        st, obj = api("DELETE", "/api/kb/%s/documents/%s/segments/%s" % (ds_id, doc_id, seg_id), token=token)
        chk(st == 200 and obj.get("ok"), "删除分段", obj)
        n_ok = False
        t0 = time.time()
        while time.time() - t0 < 60:
            st, obj = api("GET", "/api/kb/%s/documents/%s/segments?limit=100" % (ds_id, doc_id), token=token)
            if (obj.get("total") or 0) == before - 1:
                n_ok = True
                break
            time.sleep(3)
        chk(n_ok, "分段数 -1（%s -> %s）" % (before, obj.get("total")))

    st, obj = api("POST", "/api/kb/%s/documents/%s/segments/%s" % (ds_id, doc_id, seg_id or "x"),
                  {"content": "   "}, token=token)
    chk(st == 400, "空分段内容被拒绝（%s）" % st)

    # ---------- 8. 删除文档 ----------
    st, obj = api("DELETE", "/api/kb/%s/documents/%s" % (ds_id, doc_id), token=token)
    chk(st == 200 and obj.get("ok"), "删除文档", obj)
    zero = False
    t0 = time.time()
    while time.time() - t0 < 60:
        st, obj = api("GET", "/api/kb/%s/documents" % ds_id, token=token)
        if (obj.get("total") or 0) == 0:
            zero = True
            break
        time.sleep(3)
    chk(zero, "文档数归零", obj.get("total"))

    # ---------- 9. 删除知识库 ----------
    st, obj = api("DELETE", "/api/kb/" + ds_id, token=token)
    chk(st == 200 and obj.get("ok"), "删除知识库")
    time.sleep(1)
    st, obj = api("GET", "/api/kb?limit=100", token=token)
    chk(not [d for d in (obj.get("datasets") or []) if d["id"] == ds_id], "列表里已找不到该知识库")
    st, obj = dify("GET", "/datasets/" + ds_id)
    chk(st in (403, 404), "Dify 侧确认该库已不存在（HTTP %s）" % st)

    # ---------- 10. 权限 ----------
    st, obj = call(BASE + "/api/kb", "GET", None, None)
    chk(st == 401, "未登录访问知识库接口被拒（%s）" % st)

    # ---------- 清理 ----------
    print("-" * 74)
    for p in ("/api/logout",):
        api("POST", p, {}, token)
    print("  临时知识库已删除；自检账号 %s 保留在本地库里（下次运行会直接复用）。" % uname)

    print("=" * 74)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  ✗ " + f)
        print("❌ 存在失败项")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
