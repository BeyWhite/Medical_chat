/* 「知识库管理 · 按钮配色」前端自检（jsdom）
   规范（方案 A · 语义三色）：
     ① 主操作   `btn pri sm`        → 文档管理 / 分段 / 新增分段
     ② 次要操作 `btn gho sm`        → 编辑、启用/禁用、归档、搜索、清空、上一页/下一页/跳转、取消
   页码数字（1 2 3 …）用专属类 `.kb-pg`（非 `.btn`，自带 `.on` 高亮），页面跳转见 verify_pager_ui.mjs。
     ③ 破坏性   `btn gho sm dgr-txt` → 删除（弹窗确认用实心 `btn dgr`）
   并且：不得再出现第 4 种按钮色（例如 `pri-txt` 青色描边）；
        两处「搜索」必须同款；危险按钮的悬停底色须与文字同色系（--danger-soft）。
   运行：JSDOM_PATH=<jsdom> node dev/verify_btn_ui.mjs
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

const DS_ID = 'ds-1111-2222', DOC_ID = 'doc-3333-4444';
const DS = { id: DS_ID, name: '急救知识库', description: '院前急救', documentCount: 1, wordCount: 1234,
  indexingTechnique: 'high_quality', embeddingModel: 'BAAI/bge-m3', state: 'enabled',
  enabledDocuments: 1, disabledDocuments: 0, archivedDocuments: 0, updatedAt: 1750000000,
  retrieval: { searchMethod: 'semantic_search', rerankingEnable: true, topK: 3, scoreThresholdEnabled: true, scoreThreshold: 0.5 } };
const DOC = { id: DOC_ID, name: '急救.txt', position: 1, enabled: true, archived: false,
  displayStatus: 'available', indexingStatus: 'completed', wordCount: 900, tokens: 1200,
  hitCount: 3, docForm: 'text_model', createdAt: 1750000000, error: null };
const SEG = { id: 'seg-1', position: 1, content: '一条分段。', answer: null, keywords: [],
  wordCount: 5, tokens: 6, hitCount: 0, enabled: true, status: 'completed', error: null };

const jb = (o, s = 200) => ({ ok: s < 400, status: s, json: async () => o, text: async () => JSON.stringify(o) });
window.fetch = async (url, opts = {}) => {
  const m = (opts.method || 'GET').toUpperCase(), u = String(url);
  if (u === '/api/kb/retrieval') return jb({ ok: true, retrieval: { ids: [], datasets: [] } });
  if (u.startsWith('/api/kb?')) return jb({ ok: true, total: 1, hasMore: false, datasets: [DS] });
  if (u === '/api/kb/' + DS_ID) return jb({ ok: true, dataset: DS });
  if (u.startsWith('/api/kb/' + DS_ID + '/documents?')) return jb({ ok: true, total: 1, hasMore: false, documents: [DOC] });
  if (m === 'GET' && u.indexOf('/segments') >= 0) return jb({ ok: true, page: 1, limit: 10, total: 1, hasMore: false, segments: [SEG] });
  return jb({ ok: false, error: 'unmocked ' + m + ' ' + u }, 404);
};
if (!window.requestAnimationFrame) window.requestAnimationFrame = (cb) => setTimeout(cb, 0);
window.eval(kbjs);

const $ = (id) => doc.getElementById(id);
const tick = (ms = 80) => new Promise((r) => setTimeout(r, ms));
let pass = 0; const fails = [];
const chk = (c, msg, d) => { if (c) { pass++; console.log('  \u2713 ' + msg); } else { fails.push(msg); console.log('  \u2717 ' + msg + (d !== undefined ? '  --> ' + JSON.stringify(d) : '')); } };
const click = (el) => el.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
const has = (el, c) => el.classList.contains(c);
const txt = (el) => el.textContent.replace(/\s+/g, '');
const listOf = (scope, sel) => Array.from(scope.querySelectorAll(sel));
const desc = (el) => txt(el) + '(' + el.className + ')';

console.log('=== 知识库管理 · 按钮配色（方案 A · 语义三色）===');

window.KB.open();
await tick(100);

/* ---------- ① 知识库卡片 ---------- */
const card = doc.querySelector('.kb-card[data-ds="' + DS_ID + '"] .kbc-acts');
chk(!!card, '① 找到知识库卡片的操作区');
const cardPri = listOf(card, '.btn.pri');
chk(cardPri.length === 1 && txt(cardPri[0]) === '文档管理', '① 主操作只有「文档管理」一个蓝色实心按钮', cardPri.map(desc));
chk(cardPri.length === 1 && !has(cardPri[0], 'gho'), '① 主操作不带 gho（实心蓝）');
const cardGho = listOf(card, '.btn.gho:not(.dgr-txt)');
chk(cardGho.length === 3, '① 三个次要操作（归档到检索库 / 编辑 / 禁用）统一灰描边', cardGho.map(desc));
chk(cardGho.every((b) => !has(b, 'pri')), '① 次要操作里没有蓝色实心');
const cardDgr = listOf(card, '.btn.dgr-txt');
chk(cardDgr.length === 1 && txt(cardDgr[0]) === '删除', '① 危险操作只有「删除」，且带 dgr-txt', cardDgr.map(desc));
chk(cardDgr.length === 1 && has(cardDgr[0], 'gho'), '① 删除按钮仍是描边款（gho + dgr-txt）');
chk(listOf(card, '.btn.pri-txt').length === 0, '① 卡片里没有第 4 种颜色 pri-txt');
chk(cardGho.some((b) => txt(b) === '归档到检索库'), '① 「归档到检索库」已并入灰色描边');

/* ---------- ② 文档行 ---------- */
click(doc.querySelector('.kb-card[data-ds="' + DS_ID + '"] [data-act="docs"]'));
await tick(100);
const row = doc.querySelector('.kb-row[data-doc="' + DOC_ID + '"] .kbr-acts');
chk(!!row, '② 找到文档行的操作区');
const rowPri = listOf(row, '.btn.pri');
chk(rowPri.length === 1 && txt(rowPri[0]) === '分段', '② 主操作只有「分段」一个蓝色实心按钮', rowPri.map(desc));
chk(listOf(row, '.btn.gho:not(.dgr-txt)').every((b) => !has(b, 'pri')), '② 次要操作统一灰描边');
chk(listOf(row, '.btn.dgr-txt').length === 1, '② 危险操作只有「删除」');
chk(listOf(row, '.btn.pri-txt').length === 0, '② 文档行没有 pri-txt');

/* ---------- ③ 分段卡片 ---------- */
click(doc.querySelector('.kb-row[data-doc="' + DOC_ID + '"] [data-act="segs"]'));
await tick(100);
const seg = doc.querySelector('.kb-seg-item[data-seg="seg-1"] .kbs-acts');
chk(!!seg, '③ 找到分段卡片的操作区');
chk(listOf(seg, '.btn.pri').length === 0, '③ 分段行没有蓝色实心（进入下一层的动作已到头）');
chk(listOf(seg, '.btn.gho:not(.dgr-txt)').length === 2, '③ 编辑 / 禁用 统一灰描边', listOf(seg, '.btn').map(desc));
chk(listOf(seg, '.btn.dgr-txt').length === 1, '③ 危险操作只有「删除」');

/* ---------- ④ 工具栏：两处搜索必须同款 ---------- */
const searchCls = $('kbSegSearch').className;
chk(has($('kbSegSearch'), 'gho') && !has($('kbSegSearch'), 'pri'), '④ 分段页「搜索」是灰描边：' + searchCls);
chk(has($('kbSegAdd'), 'pri'), '④ 「新增分段」保持蓝色实心主操作：' + $('kbSegAdd').className);
chk(!has($('kbSegAdd'), 'gho'), '④ 「新增分段」不带 gho');
click($('kbBack'));          // 分段 → 文档
await tick(100);
click($('kbBack'));          // 文档 → 知识库列表
await tick(100);
chk($('kbSearchBtn').className === searchCls, '④ 知识库页「搜索」与分段页完全同款（原来一个蓝实心一个灰）',

  { ds: $('kbSearchBtn').className, segs: searchCls });
chk($('kbClearBtn') === null || has($('kbClearBtn'), 'gho'), '④ 「清空」也是灰描边');

/* ---------- ⑤ 页脚翻页 ---------- */
const pg = listOf(doc.querySelector('.kb-pager'), '.btn');
chk(pg.length >= 2 && pg.every((b) => has(b, 'gho') && !has(b, 'pri')),
  '⑤ 翻页区 .btn（上一页 / 下一页 / 跳转）一律灰描边', pg.map(desc));
const pgNums = listOf(doc.querySelector('.kb-pager'), '.kb-pg');
chk(pgNums.length > 0 && pgNums.every((b) => !has(b, 'btn')),
  '⑤ 页码按钮用专属 .kb-pg（不占 .btn，三色规范不受影响）', pgNums.length);
chk(listOf(doc.querySelector('.kb-pager'), '.kb-pg.on').length === 1,
  '⑤ 当前页页码有且只有一个 .on 高亮');

/* ---------- ⑥ 弹窗：取消灰、确认蓝、危险确认橙 ---------- */
click(doc.querySelector('.kb-card[data-ds="' + DS_ID + '"] [data-act="edit"]'));
await tick(60);
let modal = listOf(doc, '.kb-modal-mask.on').pop();
chk(!!modal, '⑥ 打开「编辑知识库」弹窗');
let foot = listOf(modal, '.kbm-foot .btn');
chk(has(foot[0], 'gho') && !has(foot[0], 'pri'), '⑥ 弹窗「取消」是灰描边：' + foot[0].className);
chk(has(foot[1], 'pri'), '⑥ 弹窗「保存」是蓝色实心：' + foot[1].className);
click(foot[0]);
await tick(40);
click(doc.querySelector('.kb-card[data-ds="' + DS_ID + '"] [data-act="del"]'));
await tick(60);
modal = listOf(doc, '.kb-modal-mask.on').pop();
foot = listOf(modal, '.kbm-foot .btn');
chk(foot.length === 2 && has(foot[0], 'gho') && has(foot[1], 'dgr'), '⑥ 删除确认弹窗：取消灰、确认实心橙', foot.map(desc));
click(foot[0]);
await tick(40);

/* ---------- ⑦ CSS 层：危险色同色系 / 不再有悬停掉色 ---------- */
chk((css.match(/--danger-soft:/g) || []).length === 2, '⑦ 浅色与深色主题都声明了 --danger-soft');
chk(/\.btn\.dgr-txt\{border-color:var\(--danger\)!important\}/.test(css), '⑦ 危险按钮默认就带橙色描边');
chk(/\.dgr-txt:hover\{border-color:var\(--danger\)!important;background:var\(--danger-soft\)!important\}/.test(css),
  '⑦ 危险按钮悬停底色改成 --danger-soft（原来用的是棕黄 --warn-bg）');
chk(/\.btn\.pri-txt:hover\{border-color:var\(--brand-2\);background:var\(--brand-soft\)\}/.test(css),
  '⑦ pri-txt 补了 hover（否则悬停会被 .btn.gho:hover 的灰边框顶掉）');
const dgrRule = css.slice(css.indexOf('.dgr-txt{'), css.indexOf('.dgr-txt{') + 400);
chk(!/warn-bg/.test(dgrRule), '⑦ 危险按钮相关规则里不再出现 --warn-bg');

/* ---------- ⑧ 源码防线：知识库不再使用 pri-txt ---------- */
chk(!/class="btn[^"]*pri-txt/.test(kbjs), '⑧ kb.js 里已无 pri-txt 按钮（注释里提到不算）');
chk(/按钮配色规范/.test(kbjs), '⑧ kb.js 顶部写明了三色规范注释');
const used = new Set((kbjs.match(/class="btn[^"]*"/g) || []).map((s) => s.slice(7, -1).replace("' + (opts.danger ? 'dgr' : 'pri') + '", 'pri|dgr')));
chk(used.size <= 5, '⑧ 知识库按钮类名组合收敛到 ' + used.size + ' 种：' + Array.from(used).join(' / '), Array.from(used));

console.log('\n结果：' + pass + ' 通过 / ' + fails.length + ' 失败');
if (fails.length) { fails.forEach((f) => console.log('  - ' + f)); process.exit(1); }
