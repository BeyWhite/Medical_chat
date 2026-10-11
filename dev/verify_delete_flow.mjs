/* 验证「回退后」的删除流程：输入库名 -> 按钮启用 -> 点击 -> 真正发出 DELETE */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { JSDOM } = require(process.env.JSDOM_PATH || 'jsdom');

const html = fs.readFileSync('static/index.html', 'utf8');
const kbjs = fs.readFileSync('static/kb.js', 'utf8');
const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://127.0.0.1:8001/' });
const { window } = dom, doc = window.document;
window.localStorage.setItem('mq50_token', 't');

const DS = { id: 'fae749c1-f8b7-40af-8ea7-b758cd4437e0', name: '妇科诊疗问答库', description: 'x',
  documentCount: 2, wordCount: 10, appCount: 1, indexingTechnique: 'high_quality',
  embeddingModel: 'BAAI/bge-m3', docForm: 'text_model', permission: 'only_me',
  createdAt: 1, updatedAt: 1,
  retrieval: { searchMethod: 'semantic_search', rerankingEnable: true, topK: 3,
    scoreThresholdEnabled: true, scoreThreshold: 0.5 },
  tags: [], state: 'enabled', enabledDocuments: 2, disabledDocuments: 0 };
const calls = [];
const jb = (o, s = 200) => ({ ok: s < 400, status: s, json: async () => o, text: async () => JSON.stringify(o) });
window.fetch = async (url, opts = {}) => {
  const m = (opts.method || 'GET').toUpperCase(), u = String(url);
  calls.push(m + ' ' + u);
  if (u.startsWith('/api/kb?')) return jb({ ok: true, total: 1, hasMore: false, datasets: [DS] });
  if (m === 'DELETE' && /^\/api\/kb\/[^/]+$/.test(u)) return jb({ ok: true });
  return jb({ ok: false, error: 'unmocked ' + m + ' ' + u }, 404);
};
if (!window.requestAnimationFrame) window.requestAnimationFrame = (cb) => setTimeout(cb, 0);
window.eval(kbjs);
const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));
const lastMask = () => { const a = doc.querySelectorAll('.kb-modal-mask'); return a.length ? a[a.length - 1] : null; };
const inMask = (s) => { const m = lastMask(); return m ? m.querySelector(s) : null; };

let pass = 0; const fails = [];
const chk = (c, msg, d) => { if (c) { pass++; console.log('  \u2713 ' + msg); } else { fails.push(msg); console.log('  \u2717 ' + msg + (d !== undefined ? '  --> ' + d : '')); } };

console.log('=== 回退后的删除流程（原始写法）===');
window.KB.open();
await tick(40);
const delBtn = doc.querySelector('.kb-card [data-act="del"]');
chk(!!delBtn, '列表渲染出「删除」按钮');
delBtn.click();
await tick(40);
const okBtn = inMask('[data-act="ok"]');
chk(!!lastMask(), '弹窗已打开');
chk(!!okBtn && okBtn.hasAttribute('disabled'), '确认按钮初始为 disabled（原始行为）',
  okBtn ? 'disabled=' + okBtn.getAttribute('disabled') : '无按钮');
const input = inMask('#kbmReq');
chk(!!input, '有输入库名的输入框');
// 输入正确库名 -> 按钮应被启用
input.value = DS.name;
input.dispatchEvent(new window.Event('input', { bubbles: true }));
await tick(20);
chk(!okBtn.hasAttribute('disabled'), '★ 输入正确库名后，按钮被启用（可以点了）',
  'disabled=' + okBtn.getAttribute('disabled'));
calls.length = 0;
okBtn.click();
await tick(80);
const del = calls.filter((c) => c.startsWith('DELETE /api/kb/'));
chk(del.length === 1, '★ 点击后真正发出了 DELETE 请求', JSON.stringify(calls));
chk(del[0] === 'DELETE /api/kb/' + DS.id, '请求地址正确', del[0]);
chk(calls.some((c) => c.startsWith('GET /api/kb?')), '删除后重新拉取列表');

console.log('');
console.log('通过 ' + pass + ' 项，失败 ' + fails.length + ' 项');
if (fails.length) { fails.forEach((f) => console.log('  ✗ ' + f)); process.exit(1); }
console.log('✅ 回退后的删除流程可用：输入库名 -> 按钮启用 -> 点击即删除');
