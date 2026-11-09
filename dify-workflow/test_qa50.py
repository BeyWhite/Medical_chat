#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""端到端实测「问答5.0」去掉来源判断后的三条路径：

  1) 无附件      : query 是医学问题、不带任何文件 -> false 分支，走 5 相似问检索回答
  2) 对话附件    : 对话框附件上传 txt             -> true 分支，入库
  2b) 同名复用   : 同一个文件名再传一次           -> 复用已有知识库（409 兜底），不报错
  3) 文件变量    : 开始节点「医学知识文件」传入   -> true 分支，入库（新增能力）

会临时建一个应用 API 密钥，测完删掉；测试知识库也会删掉。
用法：python test_qa50.py
"""
import json
import random
import string
import subprocess
import sys
import time
import urllib.error
import urllib.request

APP_ID = "1d7f324b-9865-4412-9e7f-53aa82decc24"
TENANT = "423da8ab-6e46-4f24-bbf0-b285e3e88c86"
API = "http://localhost/v1"
PG = ["docker", "exec", "-i", "docker-db_postgres-1", "psql", "-U", "postgres",
      "-d", "dify", "-t", "-A"]
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

SAMPLE = """中暑急救要点

一、先兆中暑：高温环境下出现头晕、口渴、多汗、四肢无力、注意力不集中，体温正常或略升高。此时应迅速离开高温环境，到阴凉通风处休息，补充含盐清凉饮料。

二、轻症中暑：体温升至 38℃ 以上，面色潮红、大量出汗、皮肤灼热，或出现面色苍白、四肢湿冷、血压下降。应迅速降温，用冷水擦拭身体，并及时送医。

三、重症中暑：包括热痉挛、热衰竭、热射病。热射病最危险，体温可达 40℃ 以上，出现意识障碍甚至昏迷，死亡率高。现场应立即转移到阴凉处，脱去衣物，用冰袋冷敷头部、腋下、腹股沟，尽快送医院抢救。

四、禁忌：中暑患者意识不清时不要强行喂水，以免呛咳误吸；不要使用酒精大面积擦拭降温。
"""


def psql(sql):
    p = subprocess.run(PG + ["-c", sql], capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise RuntimeError(p.stderr)
    return p.stdout.strip()


def token_create():
    tok = "app-" + "".join(random.choices(string.ascii_letters + string.digits, k=24))
    psql("INSERT INTO api_tokens (app_id, type, token, tenant_id) VALUES "
         "('%s','app','%s','%s');" % (APP_ID, tok, TENANT))
    return tok


def call(method, path, payload=None, token=None, raw=None, ctype="application/json"):
    data = raw if raw is not None else (
        json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None)
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    if data is not None:
        req.add_header("Content-Type", ctype)
    try:
        with _OPENER.open(req, timeout=900) as r:
            body = r.read().decode("utf-8")
            try:
                return r.status, json.loads(body)
            except json.JSONDecodeError:
                return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def upload(token, filename, content):
    b = "----wb" + "".join(random.choices(string.ascii_lowercase, k=16))
    body = b""
    body += ("--%s\r\nContent-Disposition: form-data; name=\"user\"\r\n\r\nwb-test\r\n" % b).encode()
    body += ("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
             "Content-Type: text/plain\r\n\r\n" % (b, filename)).encode("utf-8")
    body += content.encode("utf-8") + b"\r\n"
    body += ("--%s--\r\n" % b).encode()
    return call("POST", "/files/upload", raw=body, token=token,
                ctype="multipart/form-data; boundary=" + b)


def chat(token, query, files=None, inputs=None):
    payload = {"inputs": inputs or {}, "query": query,
               "response_mode": "blocking", "user": "wb-test"}
    if files:
        payload["files"] = files
    t0 = time.time()
    c, r = call("POST", "/chat-messages", token=token, payload=payload)
    return c, r, time.time() - t0


def last_run_nodes():
    rid = psql("SELECT id FROM workflow_runs WHERE app_id='%s' ORDER BY created_at DESC LIMIT 1;"
               % APP_ID)
    txt = psql("SELECT title||' | '||status||' | '||coalesce(error,'') "
               "FROM workflow_node_executions WHERE workflow_run_id='%s' ORDER BY created_at;"
               % rid)
    return rid, txt


def real_failures(nodes):
    """真正的节点失败：状态列是 failed。

    注意 exception 是「error_strategy=default-value」兜住的，属预期
    （新建知识库撞 409 时就是这个状态，错误文案里也带 'failed' 字样，
    所以不能用子串判断）。
    """
    out = []
    for ln in nodes.splitlines():
        parts = [x.strip() for x in ln.split(" | ")]
        if len(parts) >= 2 and parts[1] == "failed":
            out.append(ln)
    return out


def wait_kb(name, timeout=90):
    ds = ""
    for _ in range(timeout // 2):
        ds = psql("SELECT id FROM datasets WHERE name='%s' ORDER BY created_at DESC LIMIT 1;" % name)
        if ds:
            break
        time.sleep(2)
    if not ds:
        return "", 0, []
    for _ in range(timeout // 2):
        st = psql("SELECT indexing_status FROM documents WHERE dataset_id='%s' LIMIT 1;" % ds)
        if st == "completed":
            break
        time.sleep(2)
    rows = psql("SELECT content FROM document_segments WHERE dataset_id='%s' ORDER BY position;" % ds)
    contents = [x.strip() for x in rows.splitlines() if x.strip()]
    return ds, len(contents), contents


def main():
    tok = token_create()
    print("临时应用密钥:", tok[:14] + "...")
    results = {}
    created = []
    try:
        # ---------- 1) 无附件 -> 检索分支 ----------
        print("\n=== 1) 无附件 -> 检索分支 ===")
        c, r, dt_ = chat(tok, "中暑的现场急救怎么处理？")
        print("  HTTP", c, " 耗时 %.1fs" % dt_)
        ans = r.get("answer", "") if isinstance(r, dict) else str(r)[:600]
        print("  回答:\n" + ans[:500])
        rid, nodes = last_run_nodes()
        print("  节点执行:")
        for ln in nodes.splitlines():
            print("    " + ln)
        used = [x.split(" | ")[0] for x in nodes.splitlines()]
        results["无附件走检索"] = (
            c == 200 and "生成五个相似问" in used and "读取文件信息" in used
            and "提示-文件无正文" not in used and not real_failures(nodes))
        print("  判定:", "✅" if results["无附件走检索"] else "❌")

        # ---------- 2) 对话附件 -> 入库 ----------
        print("\n=== 2) 对话附件 -> 入库 ===")
        name_a = "中暑急救要点.txt"
        c, r = upload(tok, name_a, SAMPLE)
        if c not in (200, 201):
            print("  上传失败:", c, r)
            raise SystemExit(1)
        fid = r["id"]
        c, r, dt_ = chat(tok, "帮我把这份资料整理进知识库",
                         files=[{"type": "document", "transfer_method": "local_file",
                                 "upload_file_id": fid}])
        print("  HTTP", c, " 耗时 %.1fs" % dt_)
        ans = r.get("answer", "") if isinstance(r, dict) else str(r)[:800]
        print("  回答（前 300 字）:\n" + ans[:300])
        rid, nodes = last_run_nodes()
        used = [x.split(" | ")[0] for x in nodes.splitlines()]
        print("  走到的节点:", " / ".join(used))
        ds, n_seg, segs = wait_kb(name_a)
        created.append(ds)
        ok_json = bool(segs) and all(s.startswith("{") and s.endswith("}") and '"question"' in s
                                     for s in segs)
        print("  知识库:", ds, " 分段数:", n_seg, " 每段一条完整 JSON:", "✅" if ok_json else "❌")
        results["对话附件入库"] = (
            c == 200 and ds and ok_json and "生成问答对" in used
            and "文档提取器(对话附件)" in used
            and not real_failures(nodes))
        print("  判定:", "✅" if results["对话附件入库"] else "❌")

        # ---------- 2b) 同名知识库再来一次 -> 应复用而不是抛 409 ----------
        print("\n=== 2b) 同名知识库再传一次 -> 复用已有库 ===")
        c, r = upload(tok, name_a, SAMPLE)
        fid3 = r["id"] if c in (200, 201) else None
        if fid3:
            c, r, dt_ = chat(tok, "帮我把这份资料整理进知识库",
                             files=[{"type": "document", "transfer_method": "local_file",
                                     "upload_file_id": fid3}])
            print("  HTTP", c, " 耗时 %.1fs" % dt_)
            ans = r.get("answer", "") if isinstance(r, dict) else str(r)[:600]
            print("  回答（前 200 字）:\n" + ans[:200])
            rid, nodes = last_run_nodes()
            used = [x.split(" | ")[0] for x in nodes.splitlines()]
            ds_again, n_seg_again, segs_again = wait_kb(name_a)
            ok_json_again = bool(segs_again) and all(
                s.startswith("{") and s.endswith("}") and '"question"' in s for s in segs_again)
            print("  走到的节点:", " / ".join(used))
            print("  知识库 id 是否没变:", ds_again == ds, " 现在的分段数:", n_seg_again)
            print("  （Dify 对同库同名文档是 upsert：返回同一个 document id 并重新索引，")
            print("    所以分段数跟着本次生成的问答对条数走，不必等于上一次）")
            results["同名库复用"] = (
                c == 200 and "复用已存在的知识库" in ans and ds_again == ds
                and ok_json_again and not real_failures(nodes))
            print("  判定:", "✅" if results["同名库复用"] else "❌")
        else:
            results["同名库复用"] = False
            print("  上传失败:", c)

        # ---------- 3) 开始节点文件变量 -> 入库 ----------
        print("\n=== 3) 开始节点「医学知识文件」变量 -> 入库 ===")
        name_b = "中暑急救要点-变量.txt"
        c, r = upload(tok, name_b, SAMPLE)
        if c not in (200, 201):
            print("  上传失败:", c, r)
            raise SystemExit(1)
        fid2 = r["id"]
        inputs = {"knowledge_file": [{"type": "document", "transfer_method": "local_file",
                                      "upload_file_id": fid2}]}
        c, r, dt_ = chat(tok, "帮我把这份资料整理进知识库", inputs=inputs)
        print("  HTTP", c, " 耗时 %.1fs" % dt_)
        if c != 200:
            print("  返回:", str(r)[:600])
        ans = r.get("answer", "") if isinstance(r, dict) else str(r)[:800]
        print("  回答（前 300 字）:\n" + ans[:300])
        rid, nodes = last_run_nodes()
        used = [x.split(" | ")[0] for x in nodes.splitlines()]
        print("  走到的节点:", " / ".join(used))
        ds2, n_seg2, segs2 = wait_kb(name_b)
        created.append(ds2)
        ok_json2 = bool(segs2) and all(s.startswith("{") and s.endswith("}") and '"question"' in s
                                      for s in segs2)
        print("  知识库:", ds2, " 分段数:", n_seg2, " 每段一条完整 JSON:", "✅" if ok_json2 else "❌")
        results["文件变量入库"] = (
            c == 200 and ds2 and ok_json2 and "文档提取器(文件变量)" in used
            and "生成问答对" in used and not real_failures(nodes))
        print("  判定:", "✅" if results["文件变量入库"] else "❌")

    finally:
        for ds in created:
            if ds:
                psql("DELETE FROM datasets WHERE id='%s';" % ds)
        psql("DELETE FROM api_tokens WHERE token='%s';" % tok)
        print("\n已清理测试知识库与临时密钥")

    print("\n=== 汇总 ===")
    for k, v in results.items():
        print("  %-14s %s" % (k, "✅ 通过" if v else "❌ 失败"))
    raise SystemExit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
