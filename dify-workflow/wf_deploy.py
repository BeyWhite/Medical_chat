# -*- coding: utf-8 -*-
"""
「问答改」工作流部署 / 回退 / 知识库同步工具。

只用标准库 + docker exec 里的 psql，不依赖任何第三方包。

用法（在本目录下执行）：
    python wf_deploy.py status                      # 看当前草稿状态
    python wf_deploy.py build                       # 生成 steps/step0..7.json
    python wf_deploy.py snapshots                   # 列出可回退的快照
    python wf_deploy.py apply 4                     # 应用第 4 步（自动先备份当前草稿）
    python wf_deploy.py apply 7 --publish -m "完整版"
    python wf_deploy.py rollback 3                  # 回退到第 3 步
    python wf_deploy.py rollback 20261010-1530_step5 --publish
    python wf_deploy.py sync-datasets --once        # 把新知识库并入检索节点并重新发布
    python wf_deploy.py sync-datasets --watch       # 常驻监听，自动同步（配合 start-kb-sync.bat）
"""

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time

import wf_build

HERE = os.path.dirname(os.path.abspath(__file__))
STEPS_DIR = os.path.join(HERE, "steps")
BACKUP_DIR = os.path.join(HERE, "backup")
INDEX_PATH = os.path.join(BACKUP_DIR, "index.json")

APP_ID = wf_build.APP_ID
APP_NAME = wf_build.APP_NAME
TENANT_ID = "423da8ab-6e46-4f24-bbf0-b285e3e88c86"
ACCOUNT_ID = "75f7a702-f87c-43c8-9007-346b69bfc318"

PG_CONTAINER = "docker-db_postgres-1"
DB_NAME = "dify"
DB_USER = "postgres"


# ------------------------------------------------------------------ psql

def _psql(sql="", *, tuples_only=False, stdin_text=None):
    """执行一段 SQL（走 stdin，避免命令行长度与转义问题），返回 stdout 文本。"""
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


def _sql_script(statements):
    """把多条 SQL 拼成脚本（用 stdin 传，避免命令行长度限制）。"""
    return "\n".join(statements) + "\n"


def _dollar(js: str, tag="difywf") -> str:
    if ("$%s$" % tag) in js:
        raise ValueError("内容里含有 dollar 引用标记，无法安全写入")
    return "$%s$%s$%s$" % (tag, js, tag)


# ------------------------------------------------------------------ 读写

def _col(expr):
    return _psql("SELECT %s FROM workflows WHERE app_id='%s' AND version='draft';"
                 % (expr, APP_ID), tuples_only=True).rstrip("\n")


def get_row():
    return {
        "id": _col("id"),
        "version": _col("version"),
        "graph": _col("graph"),
        "features": _col("features"),
        "type": _col("type"),
        "version_number": _col("version_number"),
    }


def get_graph():
    return json.loads(get_row()["graph"])


def get_features():
    return json.loads(get_row()["features"])


def build_features(step: int):
    f = get_features()
    fu = f.get("file_upload") or {}
    if step >= 1:
        fu["enabled"] = True
        fu["allowed_file_types"] = ["document"]
        fu["allowed_file_extensions"] = list(wf_build.DOC_EXTENSIONS)
        fu["allowed_file_upload_methods"] = ["local_file", "remote_url"]
        fu["number_limits"] = 1
    f["file_upload"] = fu
    return f


def write_draft(graph, features):
    s = _sql_script([
        "UPDATE workflows SET graph = %s, features = %s, updated_at = now() "
        "WHERE app_id='%s' AND version='draft';"
        % (_dollar(json.dumps(graph, ensure_ascii=False), "difyg"),
           _dollar(json.dumps(features, ensure_ascii=False), "difyf"),
           APP_ID)
    ])
    _psql("", stdin_text=s)


# ------------------------------------------------------------------ 快照

def _load_index():
    if os.path.exists(INDEX_PATH):
        with open(INDEX_PATH, encoding="utf-8") as f:
            return json.load(f)
    return []


BASELINE_PATH = os.path.join(BACKUP_DIR, "sync_baseline.json")


def _load_baseline():
    """返回"视为已存在、不自动并入检索节点"的知识库 id 集合；文件不存在返回 None。"""
    if os.path.exists(BASELINE_PATH):
        with open(BASELINE_PATH, encoding="utf-8") as f:
            return set(json.load(f).get("ignore", []))
    return None


def _save_baseline(ids):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    with open(BASELINE_PATH, "w", encoding="utf-8") as f:
        json.dump({"ignore": sorted(ids)}, f, ensure_ascii=False, indent=1)


def _save_index(idx):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)


def snapshot(label):
    """把当前草稿存成一份快照，返回快照名。"""
    row = get_row()
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = "".join(c for c in label if c.isalnum() or c in "-_") or "snap"
    name = "%s_%s" % (ts, safe)
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


def list_snapshots():
    idx = _load_index()
    rows = []
    for it in idx:
        rows.append(it)
    # steps 也可回退
    for s in sorted(wf_build.STEP_TITLES):
        if os.path.exists(os.path.join(STEPS_DIR, "step%d.json" % s)):
            rows.append({"name": "step%d" % s, "at": "-",
                         "label": wf_build.STEP_TITLES[s]})
    return rows


# ------------------------------------------------------------------ 发布

def publish(marked_name="", marked_comment=""):
    row = get_row()
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


# ------------------------------------------------------------------ 应用

def apply_step(step, do_publish=False, note=""):
    path = os.path.join(STEPS_DIR, "step%d.json" % step)
    if not os.path.exists(path):
        raise SystemExit("找不到 %s，先跑 python wf_deploy.py build" % path)
    with open(path, encoding="utf-8") as f:
        graph = json.load(f)
    snap = snapshot("before_step%d" % step)
    features = build_features(step)
    write_draft(graph, features)
    msg = "已应用 step%d（%s）。应用前快照：%s" % (
        step, wf_build.STEP_TITLES.get(step, ""), snap)
    if do_publish:
        wid, vnum, ver = publish("step%d" % step, note)
        msg += "\n已发布：version#%d（%s）" % (vnum, wid[:8])
    return msg


def rollback(target, do_publish=False):
    # 先备份现状，保证"回退"本身也可回退
    snap = snapshot("before_rollback_to_%s" % target)
    if target.startswith("step") or (target.isdigit() and
                                     os.path.exists(os.path.join(STEPS_DIR, "step%s.json" % target))):
        n = target[4:] if target.startswith("step") else target
        with open(os.path.join(STEPS_DIR, "step%s.json" % n), encoding="utf-8") as f:
            graph = json.load(f)
        features = build_features(int(n))
        label = "step%s" % n
    else:
        p = os.path.join(BACKUP_DIR, target + ".json")
        if not os.path.exists(p):
            raise SystemExit("找不到快照 %s" % p)
        with open(p, encoding="utf-8") as f:
            obj = json.load(f)
        graph, features = obj["graph"], obj["features"]
        label = target
    write_draft(graph, features)
    msg = "已回退到 %s。回退前快照：%s" % (label, snap)
    if do_publish:
        wid, vnum, ver = publish("rollback-%s" % label, "")
        msg += "\n已发布：version#%d" % vnum
    return msg


# ------------------------------------------------------------------ 知识库同步

def _retrieval_node_ids(graph):
    for n in graph["nodes"]:
        if n["data"].get("type") == "knowledge-retrieval":
            return n
    return None


def sync_datasets(verbose=True):
    """
    把"改造后新出现的知识库"并入草稿的「知识检索」节点，如有变化则重新发布。

    首次运行会把当前已存在的知识库记为基线（不自动并入），只有之后新建的才会同步，
    避免把你原本故意排除在检索之外的知识库也拉进来。
    """
    rows = _psql("SELECT id FROM datasets ORDER BY created_at;", tuples_only=True)
    all_ids = [x.strip() for x in rows.splitlines() if x.strip()]
    baseline = _load_baseline()
    if baseline is None:
        _save_baseline(all_ids)
        if verbose:
            print("首次运行：已把当前 %d 个知识库记为基线，之后新建的才会自动同步。"
                  % len(all_ids))
        baseline = set(all_ids)
    graph = get_graph()
    node = _retrieval_node_ids(graph)
    if not node:
        raise RuntimeError("草稿里没有找到「知识检索」节点")
    cur = list(node["data"].get("dataset_ids") or [])
    add = [i for i in all_ids if i not in cur and i not in baseline]
    if not add:
        if verbose:
            print("没有需要同步的新知识库（检索节点现有 %d 个）。" % len(cur))
        return False
    node["data"]["dataset_ids"] = cur + add
    write_draft(graph, get_features())
    _save_baseline(baseline | set(add))
    wid, vnum, ver = publish("auto-sync-datasets", "新增知识库：%d 个" % len(add))
    if verbose:
        print("已并入 %d 个新知识库，发布 version#%d：%s"
              % (len(add), vnum, ", ".join(x[:8] for x in add)))
    return True


# ------------------------------------------------------------------ CLI

def main():
    ap = argparse.ArgumentParser(description="「问答改」工作流部署工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("build")
    sub.add_parser("status")
    sub.add_parser("snapshots")
    sub.add_parser("publish")

    p = sub.add_parser("apply")
    p.add_argument("step", type=int)
    p.add_argument("--publish", action="store_true")
    p.add_argument("-m", "--note", default="")

    p = sub.add_parser("rollback")
    p.add_argument("target")
    p.add_argument("--publish", action="store_true")

    p = sub.add_parser("sync-datasets")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--once", action="store_true")
    g.add_argument("--watch", action="store_true")
    p.add_argument("--interval", type=int, default=8)

    sub.add_parser("baseline-init")

    a = ap.parse_args()

    if a.cmd == "build":
        for s, path, nn, ne in wf_build.dump_steps(STEPS_DIR):
            print("step%d  %-46s nodes=%-3d edges=%-3d" % (s, wf_build.STEP_TITLES[s], nn, ne))

    elif a.cmd == "status":
        row = get_row()
        g = json.loads(row["graph"])
        types = {}
        for n in g["nodes"]:
            types[n["data"].get("type")] = types.get(n["data"].get("type"), 0) + 1
        app = _psql("SELECT workflow_id, name, mode FROM apps WHERE id='%s';" % APP_ID,
                    tuples_only=True).strip()
        print("应用：%s" % app)
        print("草稿工作流 id：%s" % row["id"])
        print("节点数：%d，边数：%d" % (len(g["nodes"]), len(g["edges"])))
        print("节点构成：%s" % json.dumps(types, ensure_ascii=False))
        print("已发布日期数：%s" % _psql(
            "SELECT COUNT(*) FROM workflows WHERE app_id='%s' AND version<>'draft';"
            % APP_ID, tuples_only=True).strip())

    elif a.cmd == "snapshots":
        for r in list_snapshots():
            print("%-34s %-12s %s" % (r["name"], r["at"], r["label"]))

    elif a.cmd == "publish":
        wid, vnum, ver = publish("manual", "")
        print("已发布 version#%d（%s）" % (vnum, wid))

    elif a.cmd == "apply":
        print(apply_step(a.step, a.publish, a.note))

    elif a.cmd == "rollback":
        print(rollback(a.target, a.publish))

    elif a.cmd == "sync-datasets":
        if a.watch:
            print("开始监听新知识库（每 %d 秒检查一次），Ctrl+C 退出 …" % a.interval)
            while True:
                try:
                    sync_datasets(verbose=True)
                except Exception as e:
                    print("[warn] %s" % e)
                time.sleep(a.interval)
        else:
            sync_datasets(verbose=True)

    elif a.cmd == "baseline-init":
        ids = [x.strip() for x in _psql(
            "SELECT id FROM datasets ORDER BY created_at;", tuples_only=True).splitlines()
            if x.strip()]
        _save_baseline(ids)
        print("已把当前 %d 个知识库写入基线：%s" % (len(ids), BASELINE_PATH))


if __name__ == "__main__":
    main()
