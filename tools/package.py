#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把项目打成一个「可以直接发给别人」的干净目录（或 zip）。

做三件事：
  1. 剔除不该外发的东西：config.json（含你的密钥）、data/（含本地账号与对话）、
     dev/（开发期自检，可选）、__pycache__、*.pyc、日志、临时文件
  2. 放入 config.example.json 作为配置模板
  3. 可选打成 zip

用法：
    python tools/package.py                 # 生成 dist/medical-qa-50/ 与 dist/medical-qa-50.zip
    python tools/package.py --no-zip        # 只要目录
    python tools/package.py --keep-dev      # 连 dev/ 一起打包
    python tools/package.py -o D:\\share     # 指定输出位置

零依赖，只用 Python 标准库。
"""
import argparse
import os
import re
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NAME = "medical-qa-50"

# 不进入分发包的文件/目录（相对项目根，按名字匹配）
EXCLUDE_NAMES = {
    "config.json",          # 含你的真实密钥
    "data",                 # 含本地账号、会话、PID
    "dev",                  # 开发期自检脚本（可用 --keep-dev 保留）
    "__pycache__",
    ".git",
    ".idea",
    ".vscode",
    "dist",
}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".log", ".tmp", ".bak", ".db", ".db-wal", ".db-shm")
EXCLUDE_PREFIX = ("._", "~$")


def should_skip(name, keep_dev=False):
    if name in EXCLUDE_NAMES:
        if name == "dev" and keep_dev:
            return False
        return True
    if name.startswith(EXCLUDE_PREFIX):
        return True
    if name.lower().endswith(EXCLUDE_SUFFIX):
        return True
    return False


def copy_tree(src, dst, keep_dev=False, stats=None):
    os.makedirs(dst, exist_ok=True)
    for entry in sorted(os.listdir(src)):
        if should_skip(entry, keep_dev):
            if stats is not None:
                stats["skipped"].append(entry)
            continue
        s = os.path.join(src, entry)
        d = os.path.join(dst, entry)
        if os.path.isdir(s):
            copy_tree(s, d, keep_dev, stats)
        else:
            shutil.copy2(s, d)
            stats["copied"] += 1 if stats is not None else 0


def zip_dir(src_dir, zip_path):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for base, _dirs, files in os.walk(src_dir):
            for f in files:
                full = os.path.join(base, f)
                rel = os.path.relpath(full, os.path.dirname(src_dir))
                z.write(full, rel)


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "dist"),
                    help="输出目录（默认 <项目>/dist）")
    ap.add_argument("--no-zip", action="store_true", help="不生成 zip")
    ap.add_argument("--keep-dev", action="store_true", help="保留 dev/ 开发自检目录")
    args = ap.parse_args()

    out_root = os.path.abspath(args.out)
    target = os.path.join(out_root, NAME)

    print("=" * 62)
    print("  打包分发包")
    print("=" * 62)
    print("  源目录 : %s" % ROOT)
    print("  输出到 : %s" % target)
    print("-" * 62)

    if os.path.exists(target):
        shutil.rmtree(target)
        print("  已清空旧的输出目录")

    stats = {"copied": 0, "skipped": []}
    copy_tree(ROOT, target, args.keep_dev, stats)

    # 配置模板：保证分发包里一定有一份可参考的样例
    example_src = os.path.join(ROOT, "config.example.json")
    if os.path.isfile(example_src):
        shutil.copy2(example_src, os.path.join(target, "config.example.json"))

    print("  已复制 %d 个文件" % stats["copied"])
    print("  已排除: %s" % (", ".join(sorted(set(stats["skipped"]))) or "无"))

    # 安全检查：确认包里没有真实密钥残留
    # 只按"真实密钥的形状"匹配（app-/dataset- 后跟一长串随机字符），
    # 并跳过扫描器自身；password_hash 之类的词只是数据库列名，不算泄漏。
    key_re = re.compile(r"\b(?:app|dataset)-[A-Za-z0-9_\-]{20,}")
    self_path = os.path.abspath(__file__)
    leaked = []
    for base, _dirs, files in os.walk(target):
        for f in files:
            p = os.path.join(base, f)
            rel = os.path.relpath(p, target)
            if os.path.abspath(p) == self_path:
                continue
            ext = os.path.splitext(f)[1].lower()
            if ext in (".db", ".sqlite", ".db-wal", ".db-shm", ".pyc", ".zip", ".png", ".jpg", ".ico"):
                leaked.append("%s（二进制/数据文件，请确认是否该外发）" % rel)
                continue
            if ext not in (".py", ".json", ".js", ".css", ".html", ".md", ".bat", ".sh", ".txt", ".yml", ".yaml"):
                continue
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                    txt = fh.read()
            except Exception:
                continue
            for hit in set(key_re.findall(txt)):
                rest = hit.split("-", 1)[1]
                if len(set(rest)) <= 2:      # 占位符（xxx / 111）不算
                    continue
                leaked.append("%s（疑似真实密钥：%s…）" % (rel, hit[:10]))

    if leaked:
        print("-" * 62)
        print("  [!] 检测到以下文件可能仍含敏感内容，请人工确认：")
        for x in leaked:
            print("      - %s" % x)
    else:
        print("  安全检查: 未发现密钥 / 数据库残留")

    zip_path = None
    if not args.no_zip:
        zip_path = target + ".zip"
        if os.path.exists(zip_path):
            os.remove(zip_path)
        zip_dir(target, zip_path)
        mb = os.path.getsize(zip_path) / 1024.0 / 1024.0
        print("-" * 62)
        print("  zip    : %s  (%.2f MB)" % (zip_path, mb))

    print("=" * 62)
    print("  完成。把这个目录（或 zip）发给对方即可。")
    print("  对方需要：Python 3.8+ 和一个自己的 Dify 应用。")
    print("  首次使用：双击 start.bat（或 ./start.sh），")
    print("           然后在网页「接口设置」里填自己的 Dify 地址与密钥。")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
