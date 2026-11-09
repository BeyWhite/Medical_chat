# -*- coding: utf-8 -*-
"""
修复「发布前检查清单」报出的 if-else 条件缺字段问题。

背景
----
Dify 1.17 前端 use-checklist.ts 里 if-else 的校验规则：

    if (!isEmptyRelatedOperator(op) &&
        (condition.varType === 'boolean' || condition.varType === 'array[boolean]'
            ? condition.value === undefined
            : !condition.value))
        errorMessages = '变量值不能为空'

3.0 / 4.0 的草稿是老脚本手写的，if-else 条件里**没有 varType**，
于是走 `!condition.value` 分支：`value` 为 `""`/`false` 时都判为"变量值必填"，
检查清单因此常驻 3 条红字，发布前提示"请解决以下问题"。

修法（最小改动，保留 is + 布尔值 的语义）
-----------------------------------------
- 补 `varType`：优先按被引用节点（code 节点）声明的 outputs 类型推断，
  取不到再按 value 的 Python 类型兜底；
- 补 `id`（编辑器生成的唯一 id）；
- 变量是布尔、但 value 是空串/缺失时，规范成 `false`
  （后端 `_convert_to_bool("")` 会 `json.loads("")` 直接抛异常）。

用法
----
    python wf_fix_ifelse.py <app_id> [--dry-run]
    python wf_fix_ifelse.py --all
"""

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(HERE, "backup")

PG = ["docker", "exec", "-i", "docker-db_postgres-1", "psql", "-U", "postgres", "-d", "dify"]

BOOLEAN_TYPES = {"boolean", "array[boolean]"}

# 需要修的应用
APPS = [
    ("问答3.0", "37d7e389-1265-456d-a102-94ecaa2f242a"),
    ("问答4.0", "caecf6d8-5f31-4c24-b426-ea84d4c7bb02"),
]


def psql(sql, tuples_only=True, stdin_text=None):
    cmd = list(PG) + (["-t", "-A"] if tuples_only else []) + ["-f", "-"]
    p = subprocess.run(
        cmd,
        input=(stdin_text if stdin_text is not None else sql),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if p.returncode != 0:
        raise RuntimeError("psql 失败：\n" + (p.stderr or p.stdout))
    return p.stdout


def dollar(js, tag):
    if "$%s$" % tag in js:
        raise ValueError("内容含 dollar 标记，无法安全写入")
    return "$%s$%s$%s$" % (tag, js, tag)


def read_draft(app_id):
    row = psql(
        "select graph from workflows where app_id='%s' and version='draft';" % app_id
    ).rstrip("\n")
    if not row:
        raise SystemExit("没有找到 %s 的草稿" % app_id)
    return json.loads(row)


def write_draft(app_id, graph):
    sql = (
        "update workflows set graph = %s, updated_at = now() "
        "where app_id='%s' and version='draft';"
        % (dollar(json.dumps(graph, ensure_ascii=False), "difyfix"), app_id)
    )
    psql("", tuples_only=False, stdin_text=sql + "\n")


def backup(app_id, name, graph):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(BACKUP_DIR, "%s_before_ifelse_fix_%s.json" % (ts, name))
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"app_id": app_id, "at": ts, "graph": graph}, f, ensure_ascii=False, indent=1)
    return path


def infer_var_type(sel, node_by_id, value):
    """按被引用变量的真实类型推断 varType。"""
    if sel and len(sel) >= 2:
        src = node_by_id.get(sel[0])
        if src is not None:
            outs = (src.get("data") or {}).get("outputs")
            if isinstance(outs, dict):
                meta = outs.get(sel[1])
                if isinstance(meta, dict) and meta.get("type"):
                    return meta["type"]
    # 兜底：按 value 的 Python 类型
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array[string]"
    return "string"


def fix_graph(graph):
    """返回 (修好的 graph, 变更明细 list)。"""
    node_by_id = {n["id"]: n for n in graph.get("nodes", [])}
    changes = []
    for n in graph.get("nodes", []):
        d = n.get("data") or {}
        if d.get("type") != "if-else":
            continue
        for ci, case in enumerate(d.get("cases") or []):
            for cond in case.get("conditions") or []:
                sel = cond.get("variable_selector")
                touched = []

                if not cond.get("id"):
                    cond["id"] = str(uuid.uuid4())
                    touched.append("+id")

                if not cond.get("varType"):
                    vt = infer_var_type(sel, node_by_id, cond.get("value"))
                    cond["varType"] = vt
                    touched.append("+varType=%s" % vt)

                if cond.get("varType") in BOOLEAN_TYPES and (
                    cond.get("value") is None or cond.get("value") == ""
                ):
                    old = cond.get("value")
                    cond["value"] = False
                    touched.append("value %r -> false" % (old,))

                if touched:
                    changes.append(
                        {
                            "node": n["id"],
                            "title": d.get("title"),
                            "case": case.get("case_id", ci),
                            "selector": sel,
                            "touched": touched,
                        }
                    )
    return graph, changes


def run(app_id, name, dry_run=False):
    print("=" * 72)
    print("### %s  (%s)" % (name, app_id))
    graph = read_draft(app_id)
    if dry_run:
        _, changes = fix_graph(json.loads(json.dumps(graph)))
        print("  [dry-run] 预计改动 %d 处" % len(changes))
        for c in changes:
            print("   - [%s] %s case=%s sel=%s : %s"
                  % (c["node"], c["title"], c["case"], c["selector"], ", ".join(c["touched"])))
        return len(changes)

    bpath = backup(app_id, name, graph)
    fixed, changes = fix_graph(graph)
    if not changes:
        print("  无需改动（已经是规范形态）")
        return 0
    write_draft(app_id, fixed)
    print("  已备份 -> %s" % bpath)
    print("  已修改 %d 处：" % len(changes))
    for c in changes:
        print("   - [%s] %s case=%s sel=%s : %s"
              % (c["node"], c["title"], c["case"], c["selector"], ", ".join(c["touched"])))
    return len(changes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("app_id", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.all:
        targets = APPS
    elif a.app_id:
        targets = [(a.app_id[:8], a.app_id)]
    else:
        targets = APPS

    total = 0
    for name, app_id in targets:
        total += run(app_id, name, a.dry_run)
    print("\n合计改动：%d 处" % total)


if __name__ == "__main__":
    main()
