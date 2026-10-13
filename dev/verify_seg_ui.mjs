/* 「新增分段」前端自检（jsdom）
   覆盖：分段页新增按钮 / 弹窗字段 / 单条与批量提交体 / 关键词 chips /
         长内容折叠展开 / 文档禁用时的拦截提示 / 面包屑
   运行：JSDOM_PATH=<jsdom> node dev/verify_seg_ui.mjs
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

const LONG = '第一段很长。'.repeat(60);            // > 260 字 → 触发折叠
const SEGS = [
  { id: 'seg-1', position: 1, content: LONG, answer: null, keywords: ['院前急救', '心肺复苏'],
    wordCount: 300, tokens: 380, hitCount: 2, enabled: true, status: 'completed', error: null },
  { id: 'seg-2', position: 2, content: '短内容一段。', answer: null, keywords: [],
    wordCount: 6, tokens: 8, hitCount: 0, enabled: true, status: 'completed', error: null }
];

const calls = [];
const jb = (o, s = 200) => ({ ok: s < 400, status: s, json: async () => o, text: async () => JSON.stringify(o) });
let docEnabled = true;
let posted = null;

window.fetch = async (url, opts = {}) => {
  const m = (opts.method || 'GET').toUpperCase(), u = String(url);
  calls.push({ m, u, body: opts.body ? JSON.parse(opts.body) : null });
  if (u === '/api/kb/retrieval') return jb({ ok: true, retrieval: { ids: [], datasets: [] } });
  if (u.startsWith('/api/kb?')) return jb({ ok: true, total: 1, hasMore: false, datasets: [DS] });
  if (u === `/api/kb/${DS_ID}`) return jb({ ok: true, dataset: DS });
  if (u.startsWith(`/api/kb/${DS_ID}/documents?`)) {
    return jb({ ok: true, total: 1, hasMore: false, documents: [Object.assign({}, DOC, { enabled: docEnabled })] });
  }
  if (m === 'GET' && u.startsWith(`/api/kb/${DS_ID}/documents/${DOC_ID}/segments`)) {
    return jb({ ok: true, page: 1, limit: 10, total: SEGS.length, hasMore: false, segments: SEGS });
  }
  if (m === 'POST' && u === `/api/kb/${DS_ID}/documents/${DOC_ID}/segments`) {
    posted = JSON.parse(opts.body);
    return jb({ ok: true, created: posted.segments ? posted.segments.length : 1, segments: [] });
  }
  return jb({ ok: false, error: 'unmocked ' + m + ' ' + u }, 404);
};
if (!window.requestAnimationFrame) window.requestAnimationFrame = (cb) => setTimeout(cb, 0);
window.eval(kbjs);

const $ = (id) => doc.getElementById(id);
const tick = (ms = 8) => new Promise((r) => setTimeout(r, ms));
let pass = 0; const fails = [];
const chk = (c, msg, d) => { if (c) { pass++; console.log('  \u2713 ' + msg); } else { fails.push(msg); console.log('  \u2717 ' + msg + (d !== undefined ? '  --> ' + JSON.stringify(d) : '')); } };
const click = (el) => el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
const lastModal = () => {
  const a = Array.from(doc.querySelectorAll('.kb-modal-mask')).filter((m) => m.classList.contains('on'));
  return a.length ? a[a.length - 1] : null;
};

console.log('=== 知识库 · 新增分段 前端交互 ===');

window.KB.open();
await tick(60);
chk(!!doc.querySelector('.kb-card[data-ds="' + DS_ID + '"]'), '知识库列表渲染出卡片');
chk(!!doc.querySelector('.kb-card .kbc-stat'), '卡片带统计块（美化）');

click(doc.querySelector('.kb-card[data-ds="' + DS_ID + '"] [data-act="docs"]'));
await tick(60);
chk(!!doc.querySelector('.kb-row[data-doc="' + DOC_ID + '"]'), '进入文档列表');

click(doc.querySelector('.kb-row[data-doc="' + DOC_ID + '"] [data-act="segs"]'));
await tick(80);

/* ---------- 分段页 ---------- */
const crumb = $('kbCrumb').textContent;
chk(crumb.indexOf('急救.txt') >= 0 && crumb.indexOf('分段') >= 0, '面包屑显示「文档名 + 分段」：' + crumb);
const addBtn = $('kbSegAdd');
chk(!!addBtn, '分段页出现「＋ 新增分段」按钮');
chk(!addBtn.disabled, '按钮保持可点（不用 disabled —— 遵项目按钮约定）');

chk(doc.querySelectorAll('.kb-seg-item').length === 2, '渲染出 2 条分段');
const chips = doc.querySelectorAll('.kbs-chip');
chk(chips.length === 2 && chips[0].textContent === '院前急救', '关键词渲染成 chips：' + (chips[0] && chips[0].textContent));

const first = doc.querySelector('.kb-seg-item');
const pre = first.querySelector('.kbs-content');
chk(pre.classList.contains('clamp'), '超长分段默认折叠（.clamp）');
const more = first.querySelector('.kbs-more');
chk(!!more, '折叠的分段有「展开全文」按钮');
click(more);
await tick(10);
chk(!pre.classList.contains('clamp') && more.textContent === '收起', '点一下展开，再点收起');

/* ---------- 新增：单条 ---------- */
// 注意：每次提交后 loadSegs() 会重建 #kbToolbar，#kbSegAdd 是新元素，
// 所以不能缓存按钮引用，必须每次重新取。
posted = null;
click($('kbSegAdd'));
await tick(30);
let mk = lastModal();
chk(!!mk && !!mk.querySelector('#ksnContent') && !!mk.querySelector('#ksnKw'), '弹窗有内容框与关键词框');
chk(/新增分段/.test(mk.querySelector('.kbm-head').textContent), '标题为「新增分段」');
mk.querySelector('#ksnContent').value = '这是一条手工新增的分段内容。';
mk.querySelector('#ksnKw').value = '高原病, 吸氧';
click(mk.querySelector('[data-act="ok"]'));
await tick(60);
chk(posted && posted.content === '这是一条手工新增的分段内容。', '单条提交 content', posted);
chk(posted && Array.isArray(posted.keywords) && posted.keywords.join('|') === '高原病|吸氧', '关键词拆成数组', posted && posted.keywords);
chk(!posted.segments, '单条不走 segments 数组');
chk(/新增/.test($('toast').textContent), '给出成功提示：' + $('toast').textContent);

/* ---------- 新增：用 --- 分隔批量 ---------- */
posted = null;
click($('kbSegAdd'));
await tick(30);
mk = lastModal();
chk(!!mk, '批量用例：弹窗已打开');
chk(doc.querySelectorAll('.kb-modal-mask').length === 1, '同一时刻只有一个弹窗（旧弹窗被立即移除）');
mk.querySelector('#ksnContent').value = '批量甲内容。\n---\n批量乙内容。\n\n---\n批量丙内容。';
click(mk.querySelector('[data-act="ok"]'));
await tick(60);
chk(posted && Array.isArray(posted.segments) && posted.segments.length === 3,
  '用 --- 分隔 → 一次提交 3 条', posted && posted.segments && posted.segments.length);
chk(posted && posted.segments[2].content === '批量丙内容。', '第三段内容正确（前后空白被去掉）', posted && posted.segments[2]);

/* ---------- 校验 ---------- */
posted = null;
click($('kbSegAdd'));
await tick(30);
mk = lastModal();
chk(!!mk, '校验用例：弹窗已打开');
mk.querySelector('#ksnContent').value = '   ';
click(mk.querySelector('[data-act="ok"]'));
await tick(40);
chk(posted === null && /不能为空/.test($('toast').textContent), '空内容不提交并提示：' + $('toast').textContent);

/* ---------- 文档禁用时拦截 ---------- */
docEnabled = false;
click($('kbBack'));            // 分段 -> 文档列表
await tick(40);
click($('kbRefresh'));         // 重新拉文档，此时文档 enabled=false
await tick(60);
chk(!!doc.querySelector('.kb-row[data-doc="' + DOC_ID + '"]'), '回到文档列表（已刷新为禁用状态）');
// 再进分段，让 st.curDoc 变成禁用状态
click(doc.querySelector('.kb-row[data-doc="' + DOC_ID + '"] [data-act="segs"]'));
await tick(60);
posted = null;
const nModal = doc.querySelectorAll('.kb-modal-mask').length;
click($('kbSegAdd'));
await tick(30);
chk(doc.querySelectorAll('.kb-modal-mask').length === nModal, '文档禁用时不弹窗');
chk(/禁用/.test($('toast').textContent), '并给出原因：' + $('toast').textContent);
chk(!!doc.querySelector('.kb-warn'), '列表顶部出现黄色提示条');

/* ---------- 分段正文字号（阅读舒适度） ---------- */
const css = fs.readFileSync('static/style.css', 'utf8');
const sizeOf = (sel) => {
  const m = css.match(new RegExp(sel.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\{[^}]*font-size:([^;}]+)'));
  return m ? m[1].trim() : null;
};
const cardFs = sizeOf('.kbs-content');
const taFs = sizeOf('.kbm-fg textarea.mono');
chk(cardFs === '14px', '分段卡片正文字号 = 14px（原来 12.5px 偏小）：' + cardFs);
chk(taFs === '14px', '分段「编辑/新增」弹窗的等宽输入框同号 14px：' + taFs);
chk(cardFs === taFs, '卡片与弹窗字号一致（同一段内容两处看起来一样大）');
chk(!/\.kbs-content\{[^}]*font-size:12\.5px/.test(css), 'CSS 里已无 12.5px 的分段正文（不会被旧值覆盖）');
chk(/\.kbs-content\{[^}]*line-height:1\.72/.test(css), '放大字号后仍保留 1.72 宽松行距');
const clamp = css.match(/\.kbs-content\.clamp\{max-height:(\d+)px/);
chk(clamp && +clamp[1] >= 148, '折叠高度同步放大到 ' + (clamp ? clamp[1] + 'px' : '缺失') + '（否则字号大了反而少看两行）');

console.log('---');
console.log('通过 ' + pass + ' 项，失败 ' + fails.length + ' 项');
if (fails.length) { fails.forEach((f) => console.log('  \u2717 ' + f)); process.exit(1); }
console.log('\u2705 全部通过');
