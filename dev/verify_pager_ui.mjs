/* 「知识库管理 · 页码跳转」前端自检（jsdom）
   覆盖：
     ① pageList() 页窗算法（≤7 全列；多页时首页+当前页±2+末页，中间省略号）
     ② 三层视图（知识库 / 文档 / 分段）页脚都渲染「上一页 + 页码 + 下一页 + 跳至第 N 页」
     ③ 页码按钮是专属 .kb-pg（不是 .btn），当前页只有一个 .on 高亮
     ④ 点页码 / 上一页 / 下一页 / 「跳转」按钮 / 输入框回车 → 都发对 page 参数
     ⑤ 越界夹取（999 → 末页；0 → 第 1 页）；空输入不跳；同页不重复请求
     ⑥ 无数据（total=0）不渲染分页器
     ⑦ CSS：页码高亮用品牌色、跳转输入框有独立样式
   运行：JSDOM_PATH=<jsdom> node dev/verify_pager_ui.mjs
*/
import fs from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { JSDOM } = require(process.env.JSDOM_PATH || 'jsdom');

const html = fs.readFileSync('static/index.html', 'utf8');
const kbjs = fs.readFileSync('static/kb.js', 'utf8');
const css = fs.readFileSync('static/style.css', 'utf8');

const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://127.0.0.1:8001/' });
const { window } = dom, doc = window.document;
window.localStorage.setItem('mq50_token', 'tok');

const DS_TOTAL = 201, DS_LIMIT = 20;   // → 11 页
const DOC_TOTAL = 45, DOC_LIMIT = 20;  // → 3 页
const SEG_TOTAL = 123, SEG_LIMIT = 10; // → 13 页

const mk = (n, pfx) => Array.from({ length: Math.min(n, 20) }, (_, i) =>
  ({ id: pfx + i, name: pfx + i + '.txt', position: i + 1, enabled: true, archived: false,
     displayStatus: 'available', indexingStatus: 'completed', wordCount: 10, tokens: 12, hitCount: 0,
     docForm: 'text_model', createdAt: 1750000000, error: null }));
const DS = { id: 'ds-1', name: '急救知识库', description: '院前急救', documentCount: DOC_TOTAL, wordCount: 1234,
  embeddingModel: 'BAAI/bge-m3', state: 'enabled', enabledDocuments: DOC_TOTAL, disabledDocuments: 0,
  archivedDocuments: 0, updatedAt: 1750000000, retrieval: { searchMethod: 'semantic_search', topK: 3 } };

const calls = [];
const jb = (o, s = 200) => ({ ok: s < 400, status: s, json: async () => o, text: async () => JSON.stringify(o) });
const pageOf = (u) => { const m = /[?&]page=(\d+)/.exec(u); return m ? +m[1] : null; };

window.fetch = async (url, opts = {}) => {
  const u = String(url);
  calls.push({ u, page: pageOf(u) });
  if (u === '/api/kb/retrieval') return jb({ ok: true, retrieval: { ids: [], datasets: [] } });
  if (u.startsWith('/api/kb?')) return jb({ ok: true, total: DS_TOTAL, hasMore: true, datasets: [DS, DS] });
  if (u.indexOf('/segments') >= 0) return jb({ ok: true, total: SEG_TOTAL, hasMore: true, segments: mk(SEG_TOTAL, 'seg') });
  if (u.indexOf('/documents?') >= 0) return jb({ ok: true, total: DOC_TOTAL, hasMore: true, documents: mk(DOC_TOTAL, 'doc') });
  return jb({ ok: false, error: 'unmocked ' + u }, 404);
};
if (!window.requestAnimationFrame) window.requestAnimationFrame = (cb) => setTimeout(cb, 0);
window.eval(kbjs);

const $ = (id) => doc.getElementById(id);
const tick = (ms = 60) => new Promise((r) => setTimeout(r, ms));
let pass = 0; const fails = [];
const chk = (c, msg, d) => { if (c) { pass++; console.log('  \u2713 ' + msg); } else { fails.push(msg); console.log('  \u2717 ' + msg + (d !== undefined ? '  --> ' + JSON.stringify(d) : '')); } };
const click = (el) => el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
const enter = (el) => el.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
const listOf = (scope, sel) => Array.from(scope.querySelectorAll(sel));
const chips = () => listOf(doc.querySelector('.kb-pager'), '.kb-pg:not(.dots)');
const chipText = () => chips().map((b) => b.textContent).join(',');
const info = () => (doc.querySelector('.kb-pageinfo') || {}).textContent || '';
const lastPage = () => calls[calls.length - 1].page;

console.log('=== 知识库管理 · 页码跳转 ===');

/* ---------- ① pageList 页窗算法（纯函数） ---------- */
const PL = window.__kb.pageList;
chk(JSON.stringify(PL(1, 5)) === '[1,2,3,4,5]', '① 页数 ≤ 7 时全列：' + JSON.stringify(PL(1, 5)));
chk(JSON.stringify(PL(4, 7)) === '[1,2,3,4,5,6,7]', '① 恰好 7 页也全列：' + JSON.stringify(PL(4, 7)));
const big1 = PL(1, 20), big20 = PL(20, 20), big10 = PL(10, 20);
chk(JSON.stringify(big1) === '[1,2,3,"…",20]', '① 首页：1 2 3 … 20 → ' + JSON.stringify(big1));
chk(JSON.stringify(big20) === '[1,"…",18,19,20]', '① 末页：1 … 18 19 20 → ' + JSON.stringify(big20));
chk(JSON.stringify(big10) === '[1,"…",8,9,10,11,12,"…",20]', '① 中间页：1 … 当前±2 … 20 → ' + JSON.stringify(big10));
chk(PL(10, 20).filter((x) => x !== '…').indexOf(10) >= 0, '① 当前页一定在页窗里');

/* ---------- 打开抽屉（知识库列表） ---------- */
window.KB.open();
await tick(120);
chk(!!doc.querySelector('.kb-card'), '知识库列表渲染出卡片');
chk(/第 1 \/ 11 页/.test(info()), '页脚信息显示 1/11 页（201 项 ÷ 20）：' + info());

/* ---------- ② 分页器结构 ---------- */
const pager = doc.querySelector('.kb-pager');
chk(!!pager, '出现分页器容器');
const btns = listOf(pager, '.btn');
chk(btns.length === 3, '翻页区有 3 个 .btn（上一页 / 下一页 / 跳转）：' + btns.length);
chk(!!pager.querySelector('[data-pg="prev"]') && !!pager.querySelector('[data-pg="next"]'), '有上一页与下一页');
chk(!!$('kbPgJump') && !!pager.querySelector('[data-pg="go"]'), '有「跳至第 N 页」输入框与「跳转」按钮');
chk(chips().length > 0, '渲染出页码按钮：' + chipText());
chk(!!doc.querySelector('.kb-pager .kb-pg.dots'), '多页时中间有省略号（11 页超出 7 → 页窗）：' + chipText() + ' …');
chk(info().indexOf('共 201 项') >= 0, '页脚带总条数：' + info());

/* ---------- ③ 页码样式归属 ---------- */
chk(listOf(pager, '.kb-pg').every((b) => !b.classList.contains('btn')),
  '③ 页码按钮不占 .btn（守住抽屉三色规范）');
const onChips = listOf(pager, '.kb-pg.on');
chk(onChips.length === 1 && onChips[0].textContent === '1', '③ 当前页只有一个 .on 高亮，且是第 1 页');
chk(listOf(pager, '.kb-pg.dots').every((b) => b.tagName === 'SPAN'), '③ 省略号是静态 span，不可点');
chk($('kbPgJump').getAttribute('max') === '11', '③ 跳转输入框 max=总页数：' + $('kbPgJump').getAttribute('max'));
chk(pager.querySelector('[data-pg="prev"]').disabled === true, '③ 第 1 页时「上一页」禁用');
chk(pager.querySelector('[data-pg="next"]').disabled === false, '③ 第 1 页时「下一页」可用');

/* ---------- ④ 点页码跳转 ---------- */
const n0 = calls.length;
click(chips().filter((b) => b.textContent === '3')[0]);
await tick(80);
chk(lastPage() === 3 && calls.length > n0, '④ 点页码「3」→ 发请求 page=3', lastPage());
chk(/第 3 \/ 11 页/.test(info()), '④ 页脚信息更新为 3/11：' + info());

/* ---------- ④ 下一页 / 上一页 ---------- */
click(doc.querySelector('[data-pg="next"]'));
await tick(80);
chk(lastPage() === 4, '④ 点「下一页」→ page=4', lastPage());
click(doc.querySelector('[data-pg="prev"]'));
await tick(80);
chk(lastPage() === 3, '④ 点「上一页」→ 回到 page=3', lastPage());

/* ---------- ④「跳转」按钮 ---------- */
$('kbPgJump').value = '9';
click(doc.querySelector('[data-pg="go"]'));
await tick(80);
chk(lastPage() === 9, '④ 输入 9 点「跳转」→ page=9', lastPage());
chk(listOf(doc.querySelector('.kb-pager'), '.kb-pg.on')[0].textContent === '9', '④ 高亮跟着跳到第 9 页');

/* ---------- ④ 回车跳转 ---------- */
$('kbPgJump').value = '5';
enter($('kbPgJump'));
await tick(80);
chk(lastPage() === 5, '④ 输入 5 后按回车 → page=5', lastPage());

/* ---------- ⑤ 越界 / 空值 / 同页 ---------- */
$('kbPgJump').value = '999';
enter($('kbPgJump'));
await tick(80);
chk(lastPage() === 11, '⑤ 输入 999 → 夹到末页 11', lastPage());
$('kbPgJump').value = '0';
enter($('kbPgJump'));
await tick(80);
chk(lastPage() === 1, '⑤ 输入 0 → 夹到第 1 页', lastPage());

const nA = calls.length;
$('kbPgJump').value = '';
click(doc.querySelector('[data-pg="go"]'));
await tick(60);
chk(calls.length === nA, '⑤ 空输入点「跳转」不发请求', { before: nA, after: calls.length });

$('kbPgJump').value = '4'; // 先到第 4 页
click(doc.querySelector('[data-pg="go"]'));
await tick(80);
const nB = calls.length;
click(listOf(doc.querySelector('.kb-pager'), '.kb-pg.on')[0]);
await tick(60);
chk(calls.length === nB, '⑤ 点当前页（已是 .on）不重复请求', { before: nB, after: calls.length });

/* ---------- ⑥ 无数据不渲染分页器 ---------- */
chk(window.__kb.pagerHtml({ page: 1, limit: 20, total: 0 }) === '', '⑥ total=0 时不分页器');

/* ---------- ⑦ 文档视图 ---------- */
click(doc.querySelector('.kb-card [data-act="docs"]'));
await tick(80);
chk(/第 1 \/ 3 页/.test(info()), '⑦ 文档列表页脚 1/3 页（45 项 ÷ 20）：' + info());
chk(!!$('kbPgJump'), '⑦ 文档列表也有跳转输入框');
$('kbPgJump').value = '3';
enter($('kbPgJump'));
await tick(80);
chk(lastPage() === 3 && /第 3 \/ 3 页/.test(info()), '⑦ 文档列表跳到末页 3/3：' + info());
chk(doc.querySelector('[data-pg="next"]').disabled === true, '⑦ 末页时「下一页」禁用');

/* ---------- ⑦ 分段视图 ---------- */
click(doc.querySelector('.kb-row [data-act="segs"]'));
await tick(80);
chk(/第 1 \/ 13 页/.test(info()), '⑦ 分段列表页脚 1/13 页（123 段 ÷ 10）：' + info());
chk($('kbPgJump').getAttribute('max') === '13', '⑦ 分段跳转输入框 max=13');
const segChip = listOf(doc.querySelector('.kb-pager'), '.kb-pg:not(.dots)').filter((b) => b.textContent === '2')[0];
chk(!!segChip, '⑦ 分段页有页码按钮 2');
click(segChip);
await tick(80);
chk(lastPage() === 2 && /第 2 \/ 13 页/.test(info()), '⑦ 分段页点页码「2」→ page=2：' + info());

/* ---------- ⑦ CSS ---------- */
chk(/\.kb-pg\.on\{[^}]*background:var\(--brand\)/.test(css), '⑦ 当前页页码用品牌色实心高亮');
chk(/\.kb-pg\.dots\{[^}]*cursor:default/.test(css), '⑦ 省略号不可点（cursor:default）');
chk(/\.kb-jump input\{[^}]*text-align:center/.test(css), '⑦ 跳转输入框有独立居中样式');
chk(/\.kb-pager\{[^}]*flex-wrap:wrap/.test(css), '⑦ 分页器可换行（窄屏不溢出）');

console.log('---');
console.log('通过 ' + pass + ' 项，失败 ' + fails.length + ' 项');
if (fails.length) { fails.forEach((f) => console.log('  \u2717 ' + f)); process.exit(1); }
console.log('\u2705 全部通过');
