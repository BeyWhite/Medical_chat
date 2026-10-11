#!/usr/bin/env bash
# =============================================================================
# 端到端验收脚本（对话 + 知识文件上传）
# 会在单次调用内启动服务 → 测试 → 关闭；用独立的临时数据库，不动你的真实账号。
#
#   用法：  bash verify.sh
#
# 注意：问答5.0 的问答分支要跑「5 个相似问 → 逐问检索 → 汇总」，单次约 3~4 分钟，
#       所以整体跑完需要 8~10 分钟。
# =============================================================================
cd "$(dirname "$0")" || exit 1

# ---- 找一个可用的 python3（不写死任何安装路径）----
PY=""
for c in python3 python py; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info[0]==3 else 1)' 2>/dev/null; then
      PY="$c"; break
    fi
  fi
done
[ -z "$PY" ] && { echo "[ERROR] 没有找到 Python 3。"; exit 1; }

# ---- 端口：不与正在运行的服务抢端口，自动找一个空闲的 ----
CFG_PORT="$("$PY" -c "import json;print(json.load(open('config.json',encoding='utf-8'))['server']['port'])" 2>/dev/null)"
[ -z "$CFG_PORT" ] && CFG_PORT=8001
PORT="$("$PY" - "$CFG_PORT" <<'PYEOF'
import socket, sys
start = int(sys.argv[1])
for p in range(start + 1, start + 40):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", p))
        print(p); break
    except OSError:
        continue
    finally:
        s.close()
else:
    print(start + 1)
PYEOF
)"
B="http://127.0.0.1:$PORT"
CHAT_TIMEOUT=600
echo "自检服务端口: $PORT（配置文件里是 $CFG_PORT，故意错开以免影响正在运行的服务）"

# ---- 用临时数据库启动服务（不动 data/app.db）----
TMPDB="./data/_verify.db"
rm -f "$TMPDB" "$TMPDB-wal" "$TMPDB-shm"
MQ50_DB="$TMPDB" MQ50_PORT="$PORT" "$PY" -u server.py > ./_srv.log 2>&1 &
SRV=$!
cleanup() {
  kill $SRV 2>/dev/null
  rm -f "$TMPDB" "$TMPDB-wal" "$TMPDB-shm" \
        ./_srv.log ./_sse_data.txt ./_sample.txt ./_sse.txt ./_sse_up.txt
}
trap cleanup EXIT
sleep 2.5

# 起不来就立刻告诉用户原因（比如没有 Python、端口被占）
if ! kill -0 $SRV 2>/dev/null; then
  echo "[ERROR] 自检服务没能启动，日志如下："
  cat ./_srv.log
  exit 1
fi

pass(){ echo "  ✓ $1"; }
fail(){ echo "  ✗ $1"; FAILED=1; }
chk(){ if [ "$1" = "$2" ]; then pass "$3"; else fail "$3（期望 $2，实得 $1）"; fi; }

code(){ curl -s --noproxy '*' -o /dev/null -w '%{http_code}' "$@"; }
jget(){ "$PY" -c "import sys,json;d=json.load(sys.stdin);print(eval('d'+sys.argv[1]))" "$1"; }

echo "=========== 1. 静态资源 ==========="
for p in / /style.css /app.js /kb.js; do
  c=$(code "$B$p"); chk "$c" "200" "GET $p"
done

echo "=========== 2. 鉴权 ==========="
c=$(code "$B/api/conversations"); chk "$c" "401" "未登录访问 /api/conversations 被拒"
c=$(code -X POST "$B/api/chat" -H 'Content-Type: application/json' -d '{"message":"hi"}'); chk "$c" "401" "未登录访问 /api/chat 被拒"
c=$(code -X POST "$B/api/upload" -F "file=@verify.sh"); chk "$c" "401" "未登录上传被拒"
c=$(code "$B/api/kb"); chk "$c" "401" "未登录访问知识库接口被拒"

echo "=========== 3. 注册 / 登录 ==========="
R=$(curl -s --noproxy '*' -X POST "$B/api/register" -H 'Content-Type: application/json' \
    -d '{"username":"verify_user","password":"verify123456"}')
TOKEN=$(printf '%s' "$R" | jget "['token']")
if [ -n "$TOKEN" ]; then pass "注册成功"; else fail "注册失败: $R"; fi
c=$(code -X POST "$B/api/register" -H 'Content-Type: application/json' -d '{"username":"verify_user","password":"verify123456"}')
chk "$c" "409" "重复注册被拒"
c=$(code -X POST "$B/api/login" -H 'Content-Type: application/json' -d '{"username":"verify_user","password":"wrongxxx"}')
chk "$c" "401" "错误密码被拒"
AUTH="Authorization: Bearer $TOKEN"

echo "=========== 4. 健康探测 ==========="
H=$(curl -s --noproxy '*' -H "$AUTH" "$B/api/health")
echo "  $H"
if printf '%s' "$H" | grep -q '"ok": true'; then pass "Dify 连通"; else fail "Dify 不通（请先在网页「接口设置」里填好密钥）"; fi

echo "=========== 5. 设置（密钥掩码） ==========="
S=$(curl -s --noproxy '*' -H "$AUTH" "$B/api/settings")
echo "  $S"
if printf '%s' "$S" | grep -qE '"apiKeyMasked": *"[^"]*…[^"]*"'; then pass "密钥已掩码"; else fail "密钥未掩码"; fi

echo "=========== 6. 知识文件上传（转发 Dify /files/upload） ==========="
printf '%s\n' \
  '中暑现场急救要点：迅速脱离高温环境，移至阴凉通风处，解开衣扣，平卧并抬高下肢。' \
  '用湿毛巾擦拭身体并扇风降温，意识清醒者可少量多次补充含盐清凉饮料。' \
  '如出现高热、意识模糊、抽搐等重症表现，应在降温的同时立即拨打 120 送医。' > ./_sample.txt
UP=$(curl -s --noproxy '*' -X POST "$B/api/upload" -H "$AUTH" -F "file=@./_sample.txt")
echo "  $UP"
FID=$(printf '%s' "$UP" | jget "['file']['id']")
if [ -n "$FID" ]; then pass "上传成功，upload_file_id=$FID"; else fail "上传失败"; fi
c=$(code -X POST "$B/api/upload" -H "$AUTH" -F "file=@verify.sh")
chk "$c" "400" "不支持的扩展名被拒"

echo "=========== 7. 会话管理 ==========="
CID=$(curl -s --noproxy '*' -X POST "$B/api/conversations" -H 'Content-Type: application/json' -H "$AUTH" -d '{}' | jget "['conversation']['id']")
if [ -n "$CID" ]; then pass "新建会话 $CID"; else fail "新建会话失败"; fi
N=$(curl -s --noproxy '*' -H "$AUTH" "$B/api/conversations" | "$PY" -c "import sys,json;print(len(json.load(sys.stdin)['conversations']))")
chk "$N" "1" "会话列表数量"

echo "=========== 8. 文件分支：知识文件入库（约 1~2 分钟） ==========="
curl -s -N --noproxy '*' --max-time "$CHAT_TIMEOUT" -X POST "$B/api/chat" \
  -H 'Content-Type: application/json' -H "$AUTH" \
  -d "{\"message\":\"\",\"conversationId\":\"$CID\",\"files\":[{\"id\":\"$FID\",\"name\":\"_sample.txt\"}],\"titleHint\":\"📎 _sample.txt\"}" > ./_sse_up.txt
echo "  事件统计：delta=$(grep -c '^event: delta' ./_sse_up.txt) final=$(grep -c '^event: final' ./_sse_up.txt) error=$(grep -c '^event: error' ./_sse_up.txt)"
[ "$(grep -c '^event: error' ./_sse_up.txt)" = "0" ] && pass "无错误事件" || fail "出现错误事件"
grep -q '已处理文件' ./_sse_up.txt && pass "命中「文件分支-回复」" || fail "未走文件入库分支"

echo "=========== 9. 问答分支：相似问多轮检索（约 3~4 分钟） ==========="
CID2=$(curl -s --noproxy '*' -X POST "$B/api/conversations" -H 'Content-Type: application/json' -H "$AUTH" -d '{}' | jget "['conversation']['id']")
curl -s -N --noproxy '*' --max-time "$CHAT_TIMEOUT" -X POST "$B/api/chat" \
  -H 'Content-Type: application/json' -H "$AUTH" \
  -d "{\"message\":\"什么是院前急救\",\"conversationId\":\"$CID2\"}" > ./_sse.txt
echo "  事件统计：delta=$(grep -c '^event: delta' ./_sse.txt) final=$(grep -c '^event: final' ./_sse.txt) meta=$(grep -c '^event: meta' ./_sse.txt) end=$(grep -c '^event: end' ./_sse.txt) error=$(grep -c '^event: error' ./_sse.txt)"
[ "$(grep -c '^event: delta' ./_sse.txt)" -gt 0 ] && pass "流式增量正常" || fail "无流式增量"
[ "$(grep -c '^event: error' ./_sse.txt)" = "0" ] && pass "无错误事件" || fail "出现错误事件"
echo "  --- meta 内容 ---"
sed -n 's/^data: //p' ./_sse.txt > ./_sse_data.txt
"$PY" - <<'PYEOF'
import json
meta=None;text=""
for line in open('./_sse_data.txt',encoding='utf-8'):
    line=line.strip()
    if not line: continue
    try: o=json.loads(line)
    except: continue
    if 'sources' in o or 'tokens' in o: meta=o
    if 'text' in o and len(o['text'])>len(text): text=o['text']
if meta:
    print('    tokens=%s  latencyMs=%s  firstTokenMs=%s  kb=%s' % (meta.get('tokens'),meta.get('latencyMs'),meta.get('firstTokenMs'),meta.get('kb')))
    for s in (meta.get('sources') or [])[:3]:
        print('    · %s | %s | score=%s' % (s.get('name'),s.get('dataset'),round(s.get('score') or 0,3)))
else:
    print('    (未收到 meta 事件)')
print('    回答前 160 字:', text[:160].replace('\n',' '))
PYEOF

echo "=========== 10. 持久化校验 ==========="
M=$(curl -s --noproxy '*' -H "$AUTH" "$B/api/conversations/$CID/messages")
printf '%s' "$M" | "$PY" -c "
import sys,json
d=json.load(sys.stdin)
ms=d['messages']
print('  会话标题:',d['title'])
print('  消息条数:',len(ms))
u=[m for m in ms if m['role']=='user']
a=[m for m in ms if m['role']=='assistant']
print('  用户消息 meta:',json.dumps(u[0]['meta'],ensure_ascii=False)[:160] if u else '无')
print('  助手消息 meta:',json.dumps(a[0]['meta'],ensure_ascii=False)[:160] if a else '无')
"
"$PY" -c "
import json,sys
d=json.loads(sys.stdin.read())
u=[m for m in d['messages'] if m['role']=='user']
a=[m for m in d['messages'] if m['role']=='assistant']
assert u and u[0]['meta'] and u[0]['meta'].get('files'),'附件信息未随用户消息落库'
assert a and a[0]['meta'] and a[0]['meta'].get('ingest'),'助手 meta 未标记 ingest'
print('  ✓ 附件信息与 ingest 标记已落库')
" <<< "$M" || fail "附件/ingest 未落库"

echo "=========== 11. 删除会话 / 登出 ==========="
c=$(code -X DELETE "$B/api/conversations/$CID" -H "$AUTH"); chk "$c" "200" "删除会话"
c=$(code -X DELETE "$B/api/conversations/$CID2" -H "$AUTH"); chk "$c" "200" "删除会话2"
N=$(curl -s --noproxy '*' -H "$AUTH" "$B/api/conversations" | "$PY" -c "import sys,json;print(len(json.load(sys.stdin)['conversations']))")
chk "$N" "0" "删除后列表为空"
curl -s --noproxy '*' -X POST "$B/api/logout" -H "$AUTH" >/dev/null
c=$(code "$B/api/conversations" -H "$AUTH"); chk "$c" "401" "登出后 token 失效"

echo "=========== 12. 清理自检产生的知识库 ==========="
# 第 8 步会经工作流在 Dify 里**真实新建**一个知识库（名称 = 上传的文件名），这里把它删掉。
"$PY" - <<'PYEOF'
import json, urllib.request

cfg = json.load(open("config.json", encoding="utf-8"))
base = (cfg.get("dify", {}).get("base_url") or "").rstrip("/")
key = (cfg.get("dify", {}).get("dataset_api_key") or "").strip()
target = "_sample.txt"

if not key or key.startswith("dataset-xxx"):
    print("  · 未配置知识库密钥，跳过清理（请到 Dify 里手动删除名为 %s 的知识库）" % target)
else:
    def call(method, path, timeout=30):
        req = urllib.request.Request(base + path, method=method,
                                     headers={"Authorization": "Bearer %s" % key})
        return urllib.request.urlopen(req, timeout=timeout)

    try:
        data = json.loads(call("GET", "/datasets?page=1&limit=100").read().decode("utf-8"))
        hits = [d for d in data.get("data", []) if d.get("name") == target]
        if not hits:
            print("  · 没有需要清理的知识库")
        for d in hits:
            call("DELETE", "/datasets/" + d["id"]).read()
            print("  ✓ 已删除测试知识库 %s（%s）" % (d["name"], d["id"]))
    except Exception as e:
        print("  · 清理失败（请到 Dify 里手动删除 %s）：%s" % (target, e))
PYEOF

rm -f ./_sample.txt
echo ""
if [ -n "$FAILED" ]; then echo "❌ 存在失败项"; else echo "✅ 全部通过"; fi
