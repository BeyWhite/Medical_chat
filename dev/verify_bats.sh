#!/usr/bin/env bash
# =============================================================================
# 启停脚本回归测试：真实验证 start.bat / stop.bat / restart.bat 能不能用
#
#   用法：bash dev/verify_bats.sh
#
# 背景：这些 .bat 曾在一次改造中被写入中文而**完全跑不起来**
#       （cmd.exe 解析 UTF-8 中文会错乱），当时因为只静态看代码、没真机运行，
#       问题一直没被发现。所以这里一律**真实运行**并做精确断言。
#
# 断言只认 "LISTENING"（TIME_WAIT 是内核残留，不代表服务在跑，曾因此误判）。
# =============================================================================
cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"
PORT=8001

pass(){ echo "  ✓ $1"; }
fail(){ echo "  ✗ $1"; FAILED=1; }
chk(){ if [ "$1" = "$2" ]; then pass "$3"; else fail "$3（期望 $2，实得 $1）"; fi; }

port_state(){
  if netstat -ano 2>/dev/null | grep ":$PORT " | grep -q "LISTENING"; then echo up; else echo down; fi
}
http_code(){
  curl -s --noproxy '*' -o /dev/null -w '%{http_code}' --max-time 6 "http://127.0.0.1:$PORT/" 2>/dev/null
}
kill_port(){
  for p in $(netstat -ano 2>/dev/null | grep ":$PORT " | grep LISTENING | awk '{print $5}' | sort -u); do
    taskkill //PID "$p" //F >/dev/null 2>&1
  done
}

echo "============================================================"
echo "  启停脚本回归（真实运行 .bat）"
echo "============================================================"

echo "=========== 0. 清场 ==========="
kill_port; sleep 2; rm -f data/server.pid
chk "$(port_state)" "down" "起始状态端口空闲"

echo "=========== 1. 所有 .bat 必须是纯 ASCII（含中文会让 cmd 解析错乱） ==========="
for f in start.bat stop.bat restart.bat 打包分发.bat; do
  if [ -f "$f" ]; then
    if LC_ALL=C grep -qP '[^\x00-\x7F]' "$f" 2>/dev/null; then
      chk "has-non-ascii" "ascii-only" "$f 含非 ASCII 字符（cmd 会解析错乱）"
    else
      pass "$f 纯 ASCII"
    fi
  fi
done

echo "=========== 2. start.bat 真实启动 ==========="
cmd //c "start.bat < nul" > ./_bat_start.log 2>&1 &
START_PID=$!
sleep 9
chk "$(port_state)" "up" "start.bat 后端口处于 LISTENING"
chk "$(http_code)" "200" "页面可访问"
if grep -q "not recognized as an internal or external command" ./_bat_start.log 2>/dev/null; then
  fail "start.bat 输出里有 cmd 解析错误（说明脚本又被写坏了）"
else
  pass "start.bat 输出无 cmd 解析错误"
fi
if grep -q "Medical QA 5.0" ./_bat_start.log 2>/dev/null; then
  pass "start.bat 打印了启动横幅"
else
  fail "start.bat 未打印启动横幅"
fi
PID_FILE="$(cat data/server.pid 2>/dev/null | tr -d '[:space:]')"
if [ -n "$PID_FILE" ]; then pass "写入了 PID 文件（PID=$PID_FILE）"; else fail "未写入 PID 文件"; fi
LIVE_PID="$(netstat -ano 2>/dev/null | grep ":$PORT " | grep LISTENING | awk '{print $5}' | head -1)"
chk "$PID_FILE" "$LIVE_PID" "PID 文件与实际监听进程一致"

echo "=========== 3. stop.bat 真实停止（走 PID 文件那条路） ==========="
cmd //c "stop.bat < nul" > ./_bat_stop.log 2>&1
grep -q "Stopping PID=${PID_FILE} from data.server.pid" ./_bat_stop.log \
  && pass "stop.bat 用 PID 文件定位到了进程" \
  || fail "stop.bat 未走 PID 文件方式（可查 ./_bat_stop.log）"
if grep -q 'PID=!PID!' ./_bat_stop.log 2>/dev/null; then
  fail "stop.bat 打印出了未展开的 !PID!（缺少 setlocal enabledelayedexpansion）"
else
  pass "延迟展开正常（没有未展开的 !PID!）"
fi
if grep -q "not recognized as an internal or external command" ./_bat_stop.log 2>/dev/null; then
  fail "stop.bat 输出里有 cmd 解析错误"
else
  pass "stop.bat 输出无 cmd 解析错误"
fi
sleep 4
chk "$(port_state)" "down" "stop.bat 后端口已释放（无 LISTENING）"
code="$(http_code)"
if [ "$code" = "000" ] || [ -z "$code" ]; then pass "页面已不可访问（服务确实停了）"; else fail "服务似乎还在（HTTP $code）"; fi
if [ -f data/server.pid ]; then fail "PID 文件未清除"; else pass "PID 文件已清除"; fi

echo "=========== 4. restart.bat 真实重启 ==========="
cmd //c "restart.bat < nul" > ./_bat_restart.log 2>&1 &
sleep 11
chk "$(port_state)" "up" "restart.bat 后端口处于 LISTENING"
chk "$(http_code)" "200" "页面可访问"
grep -q "\[1/2\]" ./_bat_restart.log && pass "restart.bat 执行了停止阶段" || fail "restart.bat 缺停止阶段"
grep -q "\[2/2\]" ./_bat_restart.log && pass "restart.bat 执行了启动阶段" || fail "restart.bat 缺启动阶段"

echo "=========== 5. 收尾（保持服务运行） ==========="
pass "服务保持在运行状态，供浏览器使用"

rm -f ./_bat_start.log ./_bat_stop.log ./_bat_restart.log
echo ""
if [ -n "$FAILED" ]; then echo "❌ 存在失败项"; exit 1; fi
echo "✅ 全部通过"
