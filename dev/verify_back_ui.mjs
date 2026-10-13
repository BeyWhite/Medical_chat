/* 「知识库管理 · 返回按钮」前端自检（jsdom）
   背景：返回按钮曾经只改面包屑、不重拉数据 → 正文（#kbBody）不动，
        用户视角就是「点了没反应」。本脚本锁死修复后的行为。
   覆盖：返回按钮可见性 / 标题 / 逐级返回并重绘正文 / 返回时重新请求 /
        保留上级筛选状态 / 根视图点击无副作用 / 面包屑同步
   运行：JSDOM_PATH=<jsdom> node dev/verify_back_ui.mjs
*/
import fs from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { JSDOM } = require(process.env.JSDOM_PATH || 'jsdom');

const html = fs.readFileSync('static/index.html', 'utf8');
const kbjs = fs.readFileSync('static/kb.js', 'utf8');
const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://127.0.0.1:8001/' });
const { window } = dom, doc = window.document;
window.localStorage.setItem('mq50_token', 'tok');

const DS_ID = 'ds-1111-2222', DOC_ID = 'doc-3333-4444';
const DS = { id: DS_ID, name: '急救知识库', description: '院前急救', documentCount: 1, wordCount: 1234,
  indexingTechnique: 'high_quality', embeddingModel: 'BAAI/bge-m3', state: 'enabled',
  enabledDocuments: 1, disabledDocuments: 0, archivedDocuments: 0, updatedAt: 1750000000,
  retrieval: { searchMethod: 'semantic_search', rerankingEnable: true, topK: 3, scoreThresholdEnabled: true, scoreThreshold: 0.5 } };
const DOC = { id: DOC_ID, name: '急救.txt', position: 1, enabled: true, archived: false,
  displayStatus: 'available', indexingStatus: 'completed', wordCount: 900, tokens: 1200,
  hitCount: 3, docForm: 'text_model', createdAt: 1750000000, error: null };
const SEGS = [{ id: 'seg-1', position: 1, content: '一条分段。', answer: null, keywords: [],
  wordCount: 5, tokens: 6, hitCount: 0, enabled: true, status: 'completed', error: null }];

const calls = [];
const jb = (o, s = 200) => ({ ok: s < 400, status: s, json: async () => o, text: async () => JSON.stringify(o) });
window.fetch = async (url, opts = {}) => {
  const m = (opts.method || 'GET').toUpperCase(), u = String(url);
  calls.push(m + ' ' + u);
  if (u === '/api/kb/retrieval') return jb({ ok: true, retrieval: { ids: [], datasets: [] } });
  if (u.startsWith('/api/kb?')) return jb({ ok: true, total: 1, hasMore: false, datasets: [DS] });
  if (u === '/api/kb/' + DS_ID) return jb({ ok: true, dataset: DS });
  if (u.startsWith('/api/kb/' + DS_ID + '/documents?')) return jb({ ok: true, total: 1, hasMore: false, documents: [DOC] });
  if (m === 'GET' && u.startsWith('/api/kb/' + DS_ID + '/documents/' + DOC_ID + '/segments')) {
    return jb({ ok: true, page: 1, limit: 10, total: SEGS.length, hasMore: false, segments: SEGS });
  }
  return jb({ ok: false, error: 'unmocked ' + m + ' ' + u }, 404);
};
if (!window.requestAnimationFrame) window.requestAnimationFrame = (cb) => setTimeout(cb, 0);
window.eval(kbjs);

const $ = (id) => doc.getElementById(id);
const tick = (ms = 70) => new Promise((r) => setTimeout(r, ms));
let pass = 0; const fails = [];
const chk = (c, msg, d) => { if (c) { pass++; console.log('  \u2713 ' + msg); } else { fails.push(msg); console.log('  \u2717 ' + msg + (d !== undefined ? '  --> ' + JSON.stringify(d) : '')); } };
const click = (el) => el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
const bodyHtml = () => $('kbBody').innerHTML;
const since = (n) => calls.slice(n);

console.log('=== 知识库管理 · 返回按钮 ===');

window.KB.open();
await tick(90);

/* ---------- ① 根视图 ---------- */
chk(!!doc.querySelector('.kb-card[data-ds="' + DS_ID + '"]'), '① 打开后停在知识库列表');
chk($('kbBack').hidden === true, '① 根视图返回按钮隐藏');
chk($('kbCrumb').textContent === '知识库管理', '① 面包屑为「知识库管理」');

/* 根视图强行点击（hidden 只影响显示，程序仍可派发事件）→ 不应有任何副作用 */
let n = calls.length;
click($('kbBack'));
await tick(40);
chk(since(n).length === 0, '① 根视图点返回不发请求（无副作用）', since(n));
chk(!!doc.querySelector('.kb-card[data-ds="' + DS_ID + '"]'), '① 根视图点返回后视图未变');

/* ---------- ② 进入文档列表 ---------- */
click(doc.querySelector('.kb-card[data-ds="' + DS_ID + '"] [data-act="docs"]'));
await tick(90);
chk(!!doc.querySelector('.kb-row[data-doc="' + DOC_ID + '"]'), '② 进入文档列表');
chk($('kbBack').hidden === false, '② 文档列表返回按钮可见');
chk($('kbBack').title === '返回知识库列表', '② 返回按钮 title 指明去向：' + $('kbBack').title);
chk($('kbBack').getAttribute('aria-label') === '返回知识库列表', '② 返回按钮带 aria-label');
chk($('kbCrumb').textContent.indexOf('文档') >= 0, '② 面包屑出现「文档」：' + $('kbCrumb').textContent);

/* 切一个筛选 tab，用来验证返回上级会保留状态 */
click(doc.querySelector('[data-docstatus="disabled"]'));
await tick(90);
chk(calls.some((c) => c.indexOf('status=disabled') >= 0), '② 切到「已禁用」筛选会带 status 请求');

/* ---------- ③ 进入分段列表 ---------- */
click(doc.querySelector('.kb-row[data-doc="' + DOC_ID + '"] [data-act="segs"]'));
await tick(90);
chk(!!doc.querySelector('.kb-seg-item[data-seg="seg-1"]'), '③ 进入分段列表');
chk($('kbBack').title === '返回文档列表', '③ 返回按钮 title 变为「返回文档列表」：' + $('kbBack').title);
chk($('kbCrumb').textContent.indexOf('分段') >= 0, '③ 面包屑出现「分段」：' + $('kbCrumb').textContent);

/* ---------- ④ 返回：分段 → 文档（核心回归点） ---------- */
n = calls.length;
click($('kbBack'));
await tick(90);
chk(!!doc.querySelector('.kb-row[data-doc="' + DOC_ID + '"]'), '④ 返回后正文重新渲染成文档列表（旧版这里不变）');
chk(!doc.querySelector('.kb-seg-item'), '④ 分段内容已从正文移除');
chk(since(n).some((c) => c.indexOf('/documents?') >= 0), '④ 返回时重新请求文档列表', since(n));
chk($('kbCrumb').textContent.indexOf('文档') >= 0 && $('kbCrumb').textContent.indexOf('急救.txt') < 0,
  '④ 面包屑回到「知识库 + 文档」：' + $('kbCrumb').textContent);
chk($('kbBack').hidden === false, '④ 文档列表里返回按钮仍可见');
const tabOn = doc.querySelector('[data-docstatus].on');
chk(tabOn && tabOn.getAttribute('data-docstatus') === 'disabled', '④ 返回上级保留原来的筛选 tab 选中态');

/* ---------- ⑤ 返回：文档 → 知识库 ---------- */
n = calls.length;
click($('kbBack'));
await tick(90);
chk(!!doc.querySelector('.kb-card[data-ds="' + DS_ID + '"]'), '⑤ 返回后正文重新渲染成知识库列表');
chk($('kbBack').hidden === true, '⑤ 回到根视图，返回按钮重新隐藏');
chk($('kbCrumb').textContent === '知识库管理', '⑤ 面包屑回到「知识库管理」');
chk(since(n).some((c) => c === 'GET /api/kb?page=1&limit=20'), '⑤ 返回时重新请求知识库列表', since(n));

/* ---------- ⑥ 再来一轮：反复进出不串台 ---------- */
n = calls.length;
click(doc.querySelector('.kb-card[data-ds="' + DS_ID + '"] [data-act="docs"]'));
await tick(90);
click(doc.querySelector('.kb-row[data-doc="' + DOC_ID + '"] [data-act="segs"]'));
await tick(90);
chk(!!doc.querySelector('.kb-seg-item[data-seg="seg-1"]'), '⑥ 二次进入分段列表正常');
click($('kbBack'));
await tick(90);
click($('kbBack'));
await tick(90);
chk(!!doc.querySelector('.kb-card[data-ds="' + DS_ID + '"]') && $('kbBack').hidden === true,
  '⑥ 连点两次返回可一路退回根视图');
chk(doc.querySelectorAll('.kb-modal-mask.on').length === 0, '⑥ 全程未残留弹窗');

/* ---------- ⑦ 关闭再打开：回到根视图 ---------- */
click($('kbClose'));
await tick(30);
chk(!$('kbDrawer').classList.contains('on'), '⑦ 关闭按钮收起抽屉');
window.KB.open();
await tick(90);
chk($('kbBack').hidden === true && !!doc.querySelector('.kb-card[data-ds="' + DS_ID + '"]'),
  '⑦ 重新打开回到根视图且返回按钮隐藏');

console.log('\n结果：' + pass + ' 通过 / ' + fails.length + ' 失败');
if (fails.length) { fails.forEach((f) => console.log('  - ' + f)); process.exit(1); }
