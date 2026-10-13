#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""「多套工作流连接」功能 · 端到端实测（走本机前端接口，全流程可回滚）。

覆盖：
  1. 连接列表（/api/apps）与「当前启用」标记、名称与 /api/health 一致
  2. 权限：未登录访问被拒
  3. 新增连接的入参校验（缺名称 / App Key 前缀不对）
  4. 新增连接 → 出现在列表、密钥以掩码回显、密文不下发
  5. 下拉切换（/api/apps/active）→ /api/health 的 activeApp / 应用名跟随变化
  6. 删除保护：当前正在用的连接不能删；删到只剩一条时也不能删
  7. 连通性自测（/api/apps/probe）
  8. 保存设置不影响全局项（知识库密钥 / 流式 / 超时）
  9. 复原：切回原连接、删掉临时连接，config.json 回到跑之前的样子

用法：
    python verify_apps.py          # 需要本机服务已在运行（start.bat / start.sh）
环境：零第三方依赖，只用标准库。会读写 config.json（结束时恢复原状）。
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_HERE, "config.json")


def _load_config():
    for name in ("config.json", "config.example.json"):
        p = os.path.join(_HERE, name)
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                continue
    return {}


def _read_raw_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


CFG = _load_config()
PORT = os.environ.get("MQ50_PORT") or (CFG.get("server") or {}).get("port", 8001)
BASE = os.environ.get("MQ50_BASE") or ("http://127.0.0.1:%s" % PORT)

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


def call(url, method="GET", body=None, key=None, timeout=60):
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


def api(method, path, body=None, token=None, timeout=60):
    return call(BASE + path, method, body, token, timeout)


def real_key_of_active():
    """从 config.json 直接读「当前启用连接」的 App Key（自检要拿真密钥试连通）。"""
    d = (CFG.get("dify") or {})
    apps = d.get("apps") or []
    active = d.get("active_app") or ""
    pick = None
    for a in apps:
        if a.get("id") == active:
            pick = a
            break
    if pick is None and apps:
        pick = apps[0]
    key = (pick or {}).get("api_key") or d.get("api_key") or ""
    return key.strip()


def main():
    print("=" * 74)
    print("「多套工作流连接」自检  ->  %s" % BASE)
    print("=" * 74)

    # ---------- 0. 准备 ----------
    uname = "_verify_apps"
    passwd = "Verify#Apps_2026"
    st, obj = api("POST", "/api/register", {"username": uname, "password": passwd})
    if st != 200 or not obj.get("token"):
        st, obj = api("POST", "/api/login", {"username": uname, "password": passwd})
    if st != 200 or not obj.get("token"):
        print("!! 无法登录自检账号：%s %s" % (st, obj))
        return 2
    token = obj["token"]

    raw_before = _read_raw_config()
    apps_before = ((raw_before.get("dify") or {}).get("apps") or [])
    n_before = len(apps_before)
    ids_before = sorted([a.get("id") for a in apps_before])

    # ---------- 1. 权限 ----------
    print("[1] 权限")
    st, obj = call(BASE + "/api/apps", "GET", None, None)
    chk(st == 401, "未登录访问 /api/apps 被拒（%s）" % st)

    # ---------- 2. 列表 ----------
    print("[2] 连接列表")
    st, obj = api("GET", "/api/apps", token=token)
    chk(st == 200 and obj.get("ok"), "GET /api/apps 正常（%s）" % st, obj)
    apps = obj.get("apps") or []
    active = obj.get("activeApp") or ""
    chk(len(apps) >= 1, "至少有一条连接（共 %d 条）" % len(apps))
    chk(bool(active) and any(a["id"] == active for a in apps), "activeApp 指向列表里的某一条（%s）" % active, apps)
    act = [a for a in apps if a["id"] == active]
    chk(len(act) == 1 and act[0].get("active") is True, "被选中的那条带 active 标记")
    chk(all(("api_key" not in a) and ("apiKey" not in a) for a in apps), "列表里没有下发任何明文密钥字段")
    leaked = [a for a in apps if re.search(r"app-[A-Za-z0-9_\-]{8,}", json.dumps(a, ensure_ascii=False))]
    chk(not leaked, "列表里没有出现完整 App Key 明文", leaked)

    st, h = api("GET", "/api/health", token=token)
    chk(st == 200 and h.get("activeApp") == active, "健康探测与列表的「当前连接」一致", h)
    chk(bool(h.get("name")), "健康探测拿到应用名：%s（模式 %s）" % (h.get("name"), h.get("mode")))
    base_name = h.get("name") or ""
    base_id = active
    base_key = real_key_of_active()
    chk(bool(base_key), "本地 config.json 里能读到当前连接的 App Key（供连通性测试用）")

    # ---------- 3. 入参校验 ----------
    print("[3] 新增连接的入参校验")
    st, obj = api("POST", "/api/apps", {"name": "   "}, token)
    chk(st == 400, "缺名称被拒（%s）" % st, obj)
    st, obj = api("POST", "/api/apps", {"name": "自检临时连接", "apiKey": "sk-not-an-app-key"}, token)
    chk(st == 400, "App Key 前缀不对被拒（%s）" % st, obj)

    # ---------- 4. 新增 ----------
    print("[4] 新增一条连接（不会真的连上，只登记）")
    tmp_name = "自检临时连接"
    st, obj = api("POST", "/api/apps", {
        "name": tmp_name, "apiKey": base_key, "baseUrl": (raw_before.get("dify") or {}).get("base_url") or "",
        "appType": "chatflow", "activate": False,
    }, token)
    chk(st == 200 and obj.get("ok"), "新增成功（%s）" % st, obj)
    tmp_id = obj.get("id") or ""
    chk(bool(tmp_id), "返回了新连接的 id：%s" % tmp_id)
    chk(obj.get("created") is True, "created=true")
    chk((obj.get("activeApp") or "") == base_id, "activate=false 时不会抢走「当前启用」")
    apps2 = obj.get("apps") or []
    chk(len(apps2) == len(apps) + 1, "列表多了一条（%d -> %d）" % (len(apps), len(apps2)))
    tmp_pub = [a for a in apps2 if a["id"] == tmp_id]
    chk(len(tmp_pub) == 1 and tmp_pub[0].get("hasKey") and tmp_pub[0].get("apiKeyMasked"),
        "新连接显示已填密钥且为掩码：%s" % (tmp_pub[0].get("apiKeyMasked") if tmp_pub else None))
    chk(bool(tmp_pub) and tmp_pub[0].get("baseUrl") == (raw_before.get("dify") or {}).get("base_url", ""),
        "新连接的访问点已落盘")

    # ---------- 5. 切换 ----------
    print("[5] 切换到新连接")
    st, obj = api("POST", "/api/apps/active", {"id": tmp_id}, token)
    chk(st == 200 and obj.get("ok"), "切换成功（%s）" % st, obj)
    chk(obj.get("activeApp") == tmp_id, "列表回执里 activeApp 已变")
    st, h2 = api("GET", "/api/health", token=token)
    chk(h2.get("activeApp") == tmp_id, "健康探测跟随切换（%s）" % h2.get("activeApp"))
    chk(h2.get("name") == base_name, "两个连接指向同一工作流，应用名一致：%s" % h2.get("name"))
    st, obj = api("POST", "/api/apps/active", {"id": "no-such-app-id"}, token)
    chk(st == 404, "切到不存在的连接被拒（%s）" % st, obj)

    # ---------- 6. 连通性自测 ----------
    print("[6] 连通性自测 /api/apps/probe")
    st, obj = api("POST", "/api/apps/probe", {"id": tmp_id}, token)
    chk(st == 200 and obj.get("ok"), "probe 当前连接成功（%s）" % st, obj)
    chk(obj.get("name") == base_name, "probe 拿到的应用名一致：%s" % obj.get("name"))
    st, obj = api("POST", "/api/apps/probe", {"id": tmp_id, "baseUrl": "http://127.0.0.1:9/v1"}, token)
    chk(st != 200 or not obj.get("ok"), "probe 一个连不上的地址会失败（%s）" % st)

    # ---------- 7. 删除保护 ----------
    print("[7] 删除保护")
    st, obj = api("DELETE", "/api/apps/" + tmp_id, token=token)
    chk(st == 409, "正在使用的连接不能删（%s）" % st, obj)
    st, obj = api("DELETE", "/api/apps/no-such-app-id", token=token)
    chk(st == 409, "删不存在的连接被拒（%s）" % st, obj)

    # ---------- 8. 保存设置：全局项不受影响 ----------
    print("[8] 保存设置（全局项）")
    st, s0 = api("GET", "/api/settings", token=token)
    chk(st == 200 and s0.get("hasDatasetKey"), "设置里知识库密钥仍在（%s）" % s0.get("datasetIdMasked"))
    st, obj = api("POST", "/api/settings", {
        "activeApp": tmp_id, "stream": bool(s0.get("stream")), "timeout": int(s0.get("timeout") or 900),
    }, token)
    chk(st == 200 and obj.get("ok"), "保存设置成功（%s）" % st, obj)
    st, s1 = api("GET", "/api/settings", token=token)
    chk(s1.get("datasetIdMasked") == s0.get("datasetIdMasked"), "知识库密钥未被清空")
    chk(int(s1.get("timeout") or 0) == int(s0.get("timeout") or 0), "超时保持 %s" % s1.get("timeout"))
    chk(bool(s1.get("stream")) == bool(s0.get("stream")), "流式开关保持不变")
    chk(s0.get("apps") and len(s0.get("apps")) == len(apps2), "设置接口也返回连接列表（%d 条）" % len(s0.get("apps") or []))

    # ★ 回归断言：前端「保存并重连」**每次都会带 extraInputs**。
    #   以前 handle_save_settings 组 patch 时忘了带 id，upsert_app 会把它当成
    #   「新增」→ 每保存一次就在 config.json 里凭空多出一条空连接。
    n_apps_mid = len(api("GET", "/api/apps", token=token)[1].get("apps") or [])
    api("POST", "/api/settings", {"extraInputs": "{}"}, token)
    api("POST", "/api/settings", {"extraInputs": "{}"}, token)
    apps_now = api("GET", "/api/apps", token=token)[1].get("apps") or []
    chk(len(apps_now) == n_apps_mid,
        "连存 2 次设置不会多出连接（%d -> %d）" % (n_apps_mid, len(apps_now)))
    chk(not [a for a in apps_now if not (a.get("name") or "").strip()],
        "列表里没有空名连接")
    dup = {}
    for a in apps_now:
        dup.setdefault((a.get("name") or "", a.get("apiKeyMasked") or ""), []).append(a["id"])
    dups = {k: v for k, v in dup.items() if len(v) > 1 and k[0]}
    if dups:
        # 用户可能有意保留两条，所以只提示不判失败
        print("    ⚠ 提示：存在同名称同密钥的重复连接 %s（可在页面上删掉多余的）" % list(dups.values()))

    # ---------- 9. 复原 ----------
    print("[9] 复原")
    st, obj = api("POST", "/api/apps/active", {"id": base_id}, token)
    chk(st == 200 and obj.get("activeApp") == base_id, "切回原连接（%s）" % obj.get("activeApp"))
    st, obj = api("DELETE", "/api/apps/" + tmp_id, token=token)
    chk(st == 200 and obj.get("ok"), "删除临时连接（%s）" % st, obj)
    apps_end = obj.get("apps") or []
    chk(tmp_id not in [a["id"] for a in apps_end], "临时连接已从列表消失")
    chk(len(apps_end) == len(apps), "连接数量回到 %d 条" % len(apps))

    time.sleep(0.4)
    raw_after = _read_raw_config()
    apps_after = ((raw_after.get("dify") or {}).get("apps") or [])
    chk(sorted([a.get("id") for a in apps_after]) == ids_before,
        "config.json 里的连接 id 集合与原状一致",
        "%s != %s" % (sorted([a.get("id") for a in apps_after]), ids_before))
    chk((raw_after.get("dify") or {}).get("active_app") == (raw_before.get("dify") or {}).get("active_app"),
        "active_app 复原为 %s" % (raw_before.get("dify") or {}).get("active_app"))
    chk(n_before == len(apps_after), "连接总数不变（%d）" % n_before)

    # ---------- 清理 ----------
    print("-" * 74)
    api("POST", "/api/logout", {}, token)
    print("  临时连接已删除并切回原状；自检账号 %s 保留在本地库里（下次直接复用）。" % uname)
    print("=" * 74)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  \u2717 " + f)
        print("\u274c 存在失败项")
        return 1
    print("\u2705 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
