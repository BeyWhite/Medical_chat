/* =========================================================================
   知识库管理 · 前端 DOM 级自检（复现并验证「删除知识库点了没反应」的修复）
   用 jsdom 真实跑 kb.js：渲染列表 → 点删除 → 弹窗交互 → 发出 DELETE
   运行：
     NODE_PATH=<托管workspace>/node_modules node verify_kb_ui.mjs
   ========================================================================= */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

// jsdom 只用于开发期自检，不属于本项目的运行依赖。
// 若本机已 `npm i jsdom`，直接运行即可；否则用 JSDOM_PATH 指向任意一份 jsdom 目录。
const require = createRequire(import.meta.url);
const { JSDOM } = require(process.env.JSDOM_PATH || 'jsdom');

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.join(HERE, 'static');

let PASS = 0;
const FAIL = [];
function ok(msg) { PASS++; console.log('  \u2713 ' + msg); }
function bad(msg) { FAIL.push(msg); console.log('  \u2717 ' + msg); }
function chk(cond, msg, detail) {
  if (cond) ok(msg); else bad(msg + (detail !== undefined ? '  → ' + detail : ''));
  return cond;
}

/* ---------------- 搭 jsdom 环境 ---------------- */
const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
const kbjs = fs.readFileSync(path.join(STATIC, 'kb.js'), 'utf8');
const css = fs.readFileSync(path.join(STATIC, 'style.css'), 'utf8');

const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://127.0.0.1:8001/' });
const { window } = dom;
const doc = window.document;

// 已登录状态
window.localStorage.setItem('mq50_token', 'fake-token');

/* ---------------- mock fetch ---------------- */
const calls = [];
const DS = {
  id: 'fae749c1-f8b7-40af-8ea7-b758cd4437e0',
  name: '妇科诊疗问答库',           // 故意用中文名，贴近真实场景
  description: '妇产科常见问题',
  documentCount: 2, wordCount: 1234, appCount: 1,
  indexingTechnique: 'high_quality',
  embeddingModel: 'BAAI/bge-m3', embeddingProvider: 'langgenius/siliconflow/siliconflow',
  embeddingAvailable: true, docForm: 'text_model', permission: 'only_me', authorName: 'me',
  createdAt: 1791700000, updatedAt: 1791700000,
  retrieval: { searchMethod: 'semantic_search', rerankingEnable: true, rerankingMode: 'reranking_model',
    rerankingProvider: 'p', rerankingModel: 'BAAI/bge-reranker-v2-m3', topK: 3,
    scoreThresholdEnabled: true, scoreThreshold: 0.5 },
  tags: [], state: 'enabled', enabledDocuments: 2, disabledDocuments: 0
};

function respond(obj, status = 200) {
  return { ok: status < 400, status, json: async () => obj, text: async () => JSON.stringify(obj) };
}

window.fetch = async (url, opts = {}) => {
  const method = (opts.method || 'GET').toUpperCase();
  const u = String(url);
  calls.push({ method, url: u });
  if (u.startsWith('/api/kb?')) {
    return respond({ ok: true, page: 1, limit: 20, total: 1, hasMore: false, datasets: [DS] });
  }
  if (method === 'DELETE' && /^\/api\/kb\/[^/]+$/.test(u)) {
    return respond({ ok: true });
  }
  return respond({ ok: false, error: '未 mock 的接口: ' + method + ' ' + u }, 404);
};

// kb.js 会调用 requestAnimationFrame（jsdom 未开启 usable resources 时可能缺失）
if (!window.requestAnimationFrame) window.requestAnimationFrame = (cb) => setTimeout(cb, 0);

// 在 window 上下文里执行 kb.js（等价于 <script src="/kb.js">）
window.eval(kbjs);

const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));
const $ = (sel) => doc.querySelector(sel);
// 取「最新」的那个弹窗（kb.js 里被顶替的弹窗会立即移除，这里同样只看最后一个）
const lastMask = () => { const a = doc.querySelectorAll('.kb-modal-mask'); return a.length ? a[a.length - 1] : null; };
const inMask = (sel) => { const m = lastMask(); return m ? m.querySelector(sel) : null; };

/* ---------------- 开跑 ---------------- */
console.log('='.repeat(74));
console.log('知识库管理 · 前端 DOM 级自检（jsdom）');
console.log('='.repeat(74));

console.log('\n【1】kb.js 是否正常装载');
chk(!!(window.KB && window.KB.open), 'window.KB.open 可用');
chk(!!(window.__kb && window.__kb.modal), 'window.__kb.modal 可用（测试钩子）');

console.log('\n【2】打开管理面板 → 渲染知识库列表');
window.KB.open();
await tick(30);
chk(calls.some((c) => c.url.startsWith('/api/kb?')), '已请求 /api/kb 列表');
const card = $('.kb-card');
chk(!!card, '列表渲染出知识库卡片');
chk(!!card && card.textContent.includes('妇科诊疗问答库'), '卡片上显示知识库名称');
const delBtn = $('.kb-card [data-act="del"]');
chk(!!delBtn, '卡片上有「删除」按钮');

console.log('\n【3】点「删除」→ 弹出危险确认框（复现用户场景）');
delBtn.click();
await tick(30);
chk(!!lastMask(), '确认弹窗已弹出');
const okBtn = inMask('[data-act="ok"]');
chk(!!okBtn, '弹窗里有确认按钮');
chk(!!okBtn && !okBtn.hasAttribute('disabled'),
  '★ 确认按钮【没有】disabled 属性（这正是点不动的根因）',
  okBtn ? 'disabled=' + okBtn.getAttribute('disabled') : '按钮不存在');
chk(!!okBtn && okBtn.textContent.includes('永久删除'), '按钮文案为「永久删除」');
chk(!!inMask('#kbmReq'), '弹窗要求输入知识库名称');
chk(!!inMask('#kbmReqState') && inMask('#kbmReqState').textContent.includes('需要输入'),
  '初始状态提示「需要输入…」', inMask('#kbmReqState') && inMask('#kbmReqState').textContent);

console.log('\n【4】不输名字直接点「永久删除」→ 必须给出反馈，不能静默');
let resolved;
let done = false;
window.__kb.state; // noop
const p = window.__kb.modal({
  title: '删除知识库', danger: true, confirmText: '永久删除', requireText: DS.name, body: '<p>x</p>'
}).then((v) => { resolved = v; done = true; });
await tick(30);
const ok2 = inMask('[data-act="ok"]');
ok2.click();
await tick(60);
chk(!!lastMask(), '弹窗【没有】被关掉（因为还没输入名字）');
chk(!done, 'Promise 未 resolve（不会误触发删除）');
const toastEl = doc.getElementById('toast');
chk(toastEl.textContent.includes('请先输入完整的知识库名称'), '★ 给出了明确的 toast 提示',
  JSON.stringify(toastEl.textContent));
chk(toastEl.className.includes('on') && toastEl.className.includes('err'), 'toast 处于错误态显示');
chk(!!inMask('#kbmReq').className.includes('shake'), '输入框被高亮抖动（引导用户）');
chk(!inMask('#kbmReqState').className.includes('ok'),
  '状态【不是】「一致」（空输入时提示的是"需要输入…"）', inMask('#kbmReqState').textContent);

console.log('\n【5】输入错误名字 → 仍不放行');
const input = inMask('#kbmReq');
input.value = '妇科诊疗问答库X';
input.dispatchEvent(new window.Event('input', { bubbles: true }));
await tick(10);
chk(inMask('#kbmReqState').className.includes('bad'), '状态仍为「不一致」', inMask('#kbmReqState').textContent);
ok2.click();
await tick(30);
chk(!done && !!lastMask(), '输入错误时依然不放行');

console.log('\n【6】输入正确名字 → 状态转正 → 确认放行');
input.value = DS.name;
input.dispatchEvent(new window.Event('input', { bubbles: true }));
await tick(10);
chk(inMask('#kbmReqState').className.includes('ok'), '★ 状态变为「名称一致」', inMask('#kbmReqState').textContent);
ok2.click();
await tick(60);
chk(done === true && resolved === true, '★ 名字一致时 resolve(true)（删除流程被真正触发）');
await tick(220);
chk(!lastMask(), '弹窗已自动移除');

console.log('\n【7】取消按钮与遮罩点击都能关掉');
const p2 = window.__kb.modal({ title: 't', body: '<p>x</p>' }).then((v) => { resolved = v; });
await tick(20);
inMask('[data-act="cancel"]').click();
await tick(30);
chk(resolved === false, '点「取消」返回 false');
resolved = undefined;
const p3 = window.__kb.modal({ title: 't', body: '<p>x</p>' }).then((v) => { resolved = v; });
await tick(20);
lastMask().click();
await tick(30);
chk(resolved === false, '点遮罩返回 false');

console.log('\n【8】全链路：走 delDs（列表按钮 → 弹窗 → DELETE 请求）');
calls.length = 0;
$('.kb-card [data-act="del"]').click();
await tick(30);
const ok3 = inMask('[data-act="ok"]');
const inp3 = inMask('#kbmReq');
inp3.value = DS.name;
inp3.dispatchEvent(new window.Event('input', { bubbles: true }));
await tick(10);
ok3.click();
await tick(80);
const del = calls.filter((c) => c.method === 'DELETE');
chk(del.length === 1, '发出了 1 个 DELETE 请求', JSON.stringify(calls));
chk(del.length === 1 && del[0].url === '/api/kb/' + DS.id, '请求地址正确', del[0] && del[0].url);
chk(calls.some((c) => c.url.startsWith('/api/kb?') && c.method === 'GET'), '删除后重新拉取了列表');

console.log('\n【9】键盘：输入框回车 = 点确认');
resolved = undefined;
const p4 = window.__kb.modal({ title: 't', confirmText: '删除', requireText: 'ABC', body: '' }).then((v) => { resolved = v; });
await tick(30);
const inp4 = inMask('#kbmReq');
inp4.value = 'ABC';
inp4.dispatchEvent(new window.Event('input', { bubbles: true }));
inp4.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
await tick(40);
chk(resolved === true, '回车也能提交');

console.log('\n【10】样式层面：关键层级与可见性');
chk(/\.toast\{z-index:200\}/.test(css), '★ toast 的 z-index 提升到 200（高于弹窗 120，提示不再被遮住）');
chk(/\.kb-modal-mask\{[^}]*z-index:120/.test(css), '弹窗遮罩 z-index 仍为 120（低于 toast，高于抽屉 95）');
chk(/\.btn:disabled[^{]*\{[^}]*grayscale/.test(css), '★ disabled 按钮统一灰化（以后不会有"看着能点其实点不动"）');
chk(!/need \? ' disabled' : ''/.test(kbjs), 'kb.js 里已不存在给确认按钮注入 disabled 的写法');

console.log('\n' + '='.repeat(74));
console.log('通过 ' + PASS + ' 项，失败 ' + FAIL.length + ' 项');
if (FAIL.length) {
  FAIL.forEach((f) => console.log('  ✗ ' + f));
  console.log('❌ 存在失败项');
  process.exit(1);
}
console.log('✅ 全部通过');
process.exit(0);
