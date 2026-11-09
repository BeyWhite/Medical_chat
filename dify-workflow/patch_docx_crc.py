# -*- coding: utf-8 -*-
"""
修补 Dify 1.17.1 的 DOCX 提取：跳过损坏的 ZIP CRC 校验。

背景
----
某些 DOCX（例如由非标准工具导出的）内部 ZIP 条目的 CRC 被写成 0，
但实际数据不匹配。python-docx 打开时会遍历读取条目做 CRC 校验，
于是抛 `Bad CRC-32 for file 'word/media/image1.jpeg'`。

Dify 自带的 `_normalize_docx_zip` 本想兜底，但它用的是
`zin.read(item.filename)`，这一步同样会触发 CRC 校验并失败，
所以兜底形同虚设。

关键点
------
Python 3.12 的 `zipfile` 在 `ZipFile.open()` 时把 `ZipInfo.CRC` 快照到
`ZipExtFile._expected_crc`，随后读取过程中逐块比对；**只有在该属性为
None 时才跳过校验**。所以必须覆盖 `open()` 去置空它，光改 `ZipInfo.CRC`
没用（会变成"期望 CRC=0，实际非 0"，照样报错）。

做法
----
在容器内替换 graphon 的 document_extractor/node.py：
  1. 加一个 `_NoCrcZipFile`（覆盖 `open()`，把 `_expected_crc` 置空）；
  2. `_normalize_docx_zip` 改用它打开，重写 ZIP 时顺便写回正确 CRC。

可回退：同目录下已备份 `node.py.bak-before-crcfix`；
也可直接执行 `python patch_docx_crc.py --revert`。

用法（在宿主机执行）：
    python patch_docx_crc.py            # 打补丁并重启 api 容器
    python patch_docx_crc.py --revert   # 还原到备份
"""

import argparse
import subprocess

CONTAINER = "docker-api-1"
NODE_PATH = ("/app/api/.venv/lib/python3.12/site-packages/graphon/"
             "nodes/document_extractor/node.py")
BAK_PATH = NODE_PATH + ".bak-before-crcfix"
PYC_DIR = ("/app/api/.venv/lib/python3.12/site-packages/graphon/"
           "nodes/document_extractor/__pycache__")

PATCH_MARK = "# --- dify-crc-fix ---"

HELPER = '''

# --- dify-crc-fix ---
import zlib as _zlib_mod  # noqa: E402


class _NoCrcZipFile(zipfile.ZipFile):
    """读取条目时不做 CRC 校验的 ZipFile。

    Python 3.12 的 ZipExtFile 在 open() 时快照 ZipInfo.CRC 到
    `_expected_crc`，读取过程中逐块比对；该属性为 None 才跳过校验。
    因此必须在 open() 返回的对象上把它置空。
    """

    def open(self, name, mode="r", pwd=None, *, force_zip64=False):
        fp = super().open(name, mode, pwd, force_zip64=force_zip64)
        for attr in ("_expected_crc", "_expected_crc32"):
            if hasattr(fp, attr):
                try:
                    setattr(fp, attr, None)
                except Exception:
                    pass
        return fp
# --- dify-crc-fix-end ---
'''


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", **kw)


def sh(script):
    return run(["docker", "exec", "-i", CONTAINER, "sh", "-c", script])


def read_file(path):
    p = run(["docker", "exec", "-i", CONTAINER, "cat", path])
    if p.returncode != 0:
        raise SystemExit("读取失败：%s\n%s" % (path, p.stderr))
    return p.stdout


def write_file(path, text):
    p = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "sh", "-c", "cat > %s" % path],
        input=text, capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise SystemExit("写入失败：%s\n%s" % (path, p.stderr))


def restart_api():
    print("重启 api 容器 …")
    p = run(["docker", "restart", CONTAINER])
    print(p.stdout.strip() or p.stderr.strip())
    print("完成。请等 20~30 秒让 Dify 起来。")


def do_patch():
    src = read_file(NODE_PATH)
    if PATCH_MARK in src:
        print("已经打过补丁，无需重复。")
        return

    if "import docx" not in src:
        raise SystemExit("没找到 `import docx`，Dify 版本可能已变化，请人工核对。")

    # 插入 helper
    idx = src.index("import docx")
    line_end = src.index("\n", idx) + 1
    src = src[:line_end] + HELPER + src[line_end:]

    # _normalize_docx_zip 用 _open_nocrc 打开
    old_norm = 'with zipfile.ZipFile(io.BytesIO(file_content), "r") as zin:'
    if old_norm not in src:
        raise SystemExit("_normalize_docx_zip 的打开语句没匹配到，请人工核对。")
    src = src.replace(old_norm,
                      'with _NoCrcZipFile(io.BytesIO(file_content), "r") as zin:',
                      1)

    # 重写条目时写回正确 CRC（避免目标 ZIP 也带坏 CRC）
    old_rewrite = (
        "    for item in zin.infolist():\n"
        "        data = zin.read(item.filename)\n"
        "        # Normalize backslash path separators to forward slash.\n"
        '        item.filename = item.filename.replace("\\\\", "/")\n'
        "        zout.writestr(item, data)\n"
    )
    new_rewrite = (
        "    for item in zin.infolist():\n"
        "        data = zin.read(item.filename)\n"
        "        # Normalize backslash path separators to forward slash.\n"
        '        item.filename = item.filename.replace("\\\\", "/")\n'
        "        # dify-crc-fix: 写回真实 CRC，避免产出仍带坏校验和的 ZIP\n"
        "        item.CRC = _zlib_mod.crc32(data) & 0xFFFFFFFF\n"
        "        zout.writestr(item, data)\n"
    )
    if old_rewrite in src:
        src = src.replace(old_rewrite, new_rewrite, 1)
    else:
        print("[warn] _rewrite_docx_zip_entries 未精确匹配，跳过 CRC 回写"
              "（不影响主要修复）。")

    write_file(NODE_PATH, src)
    print("补丁已写入 %s" % NODE_PATH)

    sh("find %s -name '*.pyc' -delete 2>/dev/null; true" % PYC_DIR)
    print("已清理 pyc 缓存。")
    restart_api()


def do_revert():
    chk = sh("test -f %s && echo yes" % BAK_PATH)
    if "yes" not in chk.stdout:
        raise SystemExit("找不到备份 %s，无法还原。" % BAK_PATH)
    sh("cp %s %s" % (BAK_PATH, NODE_PATH))
    sh("find %s -name '*.pyc' -delete 2>/dev/null; true" % PYC_DIR)
    print("已还原到备份。")
    restart_api()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--revert", action="store_true")
    a = ap.parse_args()
    do_revert() if a.revert else do_patch()
