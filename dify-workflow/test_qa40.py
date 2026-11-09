#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""端到端实测「问答4.0」文件分支写库：上传文件 -> 生成问答对 -> 写入知识库，
然后检查知识库里每个分段是否正好等于一条 JSON 语句。

会临时建一个应用 API 密钥，测完删掉；测试知识库也会删掉。

用法：python test_qa40.py
"""
import json
import random
import string
import subprocess
import sys
import time
import urllib.error
import urllib.request

APP_ID = "caecf6d8-5f31-4c24-b426-ea84d4c7bb02"
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


def psql(sql, tuples=True):
    cmd = PG + (["-c", sql] if not sql.strip().startswith("--") else [])
    p = subprocess.run(PG + ["-c", sql], capture_output=True, text=True, encoding="utf-8")
    return p.stdout.strip()


def token_create():
    tok = "app-" + "".join(random.choices(string.ascii_letters + string.digits, k=24))
    psql("INSERT INTO api_tokens (app_id, type, token, tenant_id) VALUES "
         "('%s','app','%s','%s');" % (APP_ID, tok, TENANT))
    return tok


def token_delete(tok):
    psql("DELETE FROM api_tokens WHERE token='%s';" % tok)


def call(method, path, payload=None, token=None, raw=None, ctype="application/json"):
    data = raw if raw is not None else (
        json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None)
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    if data is not None:
        req.add_header("Content-Type", ctype)
    try:
        with _OPENER.open(req, timeout=600) as r:
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


def main():
    tok = token_create()
    print("临时应用密钥已建:", tok[:12] + "...")
    kb_name = "中暑急救要点.txt"
    try:
        c, r = upload(tok, kb_name, SAMPLE)
        print("上传文件 ->", c)
        if c not in (200, 201):
            print(r)
            raise SystemExit(1)
        fid = r["id"]

        print("发起对话（文件分支，blocking）…")
        t0 = time.time()
        c, r = call("POST", "/chat-messages", token=tok, payload={
            "inputs": {}, "query": "请把这份医学知识文件整理进知识库",
            "response_mode": "blocking", "user": "wb-test",
            "files": [{"type": "document", "transfer_method": "local_file",
                       "upload_file_id": fid}],
        })
        print("  对话 ->", c, " 耗时 %.1fs" % (time.time() - t0))
        if c != 200:
            print(r)
            raise SystemExit(1)
        print("  回答:\n" + r.get("answer", "")[:1200])

        # 等索引完成
        ds = ""
        for _ in range(40):
            ds = psql("SELECT id FROM datasets WHERE name='%s' ORDER BY created_at DESC LIMIT 1;" % kb_name)
            if ds:
                break
            time.sleep(2)
        print("\n知识库 id:", ds)
        for _ in range(40):
            st = psql("SELECT indexing_status FROM documents WHERE dataset_id='%s' LIMIT 1;" % ds)
            if st == "completed":
                break
            time.sleep(2)
        print("文档状态:", psql("SELECT name||' | '||indexing_status||' | seg='||"
                            "(SELECT count(*) FROM document_segments s WHERE s.document_id=d.id) "
                            "FROM documents d WHERE dataset_id='%s';" % ds))
        print("\n各分段内容（每段应正好一条 JSON 语句）：")
        rows = psql("SELECT position||' :: '||content FROM document_segments WHERE dataset_id='%s' "
                    "ORDER BY position;" % ds)
        contents = []
        for line in rows.splitlines():
            if " :: " in line:
                _, _, c = line.partition(" :: ")
                contents.append(c.strip())
            print("   ", line[:160])
        n = int(psql("SELECT count(*) FROM document_segments WHERE dataset_id='%s';" % ds) or 0)
        ok = bool(contents) and n == len(contents) and all(
            c.startswith("{") and c.endswith("}") and '"question"' in c for c in contents)
        print("\n分段数 =", n, " 每段都是完整 JSON 对象:", "✅" if ok else "❌")

        psql("DELETE FROM datasets WHERE id='%s';" % ds)
        print("已清理测试知识库")
    finally:
        token_delete(tok)
        print("已删除临时应用密钥")


if __name__ == "__main__":
    main()
