/* 「归档到检索库」前端流程自检（jsdom）。
 *
 * 覆盖：检索库徽章 / 归档按钮的显隐 / 目标库下拉 / 确认后真的发出 POST /
 *       归档完成回执弹窗 / 编辑弹窗里的「列入检索库」开关。
 *
 * 运行：
 *   JSDOM_PATH=/path/to/jsdom node dev/verify_archive_ui.mjs
 */
import fs from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { JSDOM } = require(process.env.JSDOM_PATH || 'jsdom');

const html = fs.readFileSync('static/index.html', 'utf8');
const kbjs = fs.readFileSync('static/kb.js', 'utf8');
const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://127.0.0.1:8001/' });
const { window } = dom;
const doc = window.document;
window.localStorage.setItem('mq50_token', 't');

const RETR_ID = '4657f032-fd37-4763-9208-1f8d907d244d';
const UP_ID = 'b759feda-19dc-4c59-a4de-5251364d10ec';

const base = (o) => Object.assign({
  description: '', wordCount: 10, appCount: 1, indexingTechnique: 'high_quality',
  embeddingModel: 'BAAI/bge-m3', docForm: 'text_model', permission: 'only_me',
  createdAt: 1, updatedAt: 1, tags: [], enabledDocuments: 1, disabledDocuments: 0,
  retrieval: { searchMethod: 'semantic_search', rerankingEnable: true, topK: 3,
    scoreThresholdEnabled: true, scoreThreshold: 0.5 }
}, o);

const RETR = base({ id: RETR_ID, name: '内科1.txt...', documentCount: 3, state: 'enabled' });
const UPLOAD = base({ id: UP_ID, name: 'test.docx', documentCount: 1, state: 'enabled' });

const calls = [];
const jb = (o, s = 200) => ({ ok: s < 400, status: s, json: async () => o, text: async () => JSON.stringify(o) });
let retrIds = [RETR_ID];
window.fetch = async (url, opts = {}) => {
  const m = (opts.method || 'GET').toUpperCase();
  const u = String(url);
  let body = null;
  try { body = opts.body ? JSON.parse(opts.body) : null; } catch (e) {}
  calls.push(m + ' ' + u + (body ? ' ' + JSON.stringify(body) : ''));
  if (u.startsWith('/api/kb/retrieval')) {
    if (m === 'POST') {
      if (body && body.add && retrIds.indexOf(body.add) < 0) retrIds = retrIds.concat([body.add]);
      if (body && body.remove) retrIds = retrIds.filter((x) => x !== body.remove);
    }
    return jb({ ok: true, retrieval: {
      ids: retrIds,
      datasets: retrIds.map((i) => ({ id: i, name: i === RETR_ID ? '内科1.txt...' : 'test.docx',
        exists: true, documentCount: 3 })) } });
  }
  if (u.startsWith('/api/kb?')) return jb({ ok: true, total: 2, hasMore: false, datasets: [RETR, UPLOAD] });
  if (m === 'POST' && /\/archive-to$/.test(u)) {
    return jb({ ok: true, target: { id: RETR_ID, name: '内科1.txt...' },
      source: { id: UP_ID, name: 'test.docx' },
      archived: [{ documentId: 'd1', name: 'test.docx', targetDocumentId: 't1', segments: 26,
                   skippedSegments: 2, chars: 4901, indexingStatus: 'completed' }],
      skipped: [], deletedSource: true });
  }
  if (m === 'PATCH' || (m === 'DELETE')) return jb({ ok: true });
  return jb({ ok: false, error: 'unmocked ' + m + ' ' + u }, 404);
};
if (!window.requestAnimationFrame) window.requestAnimationFrame = (cb) => setTimeout(cb, 0);
window.eval(kbjs);

const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));
const masks = () => Array.from(doc.querySelectorAll('.kb-modal-mask'));
const lastMask = () => { const a = masks(); return a.length ? a[a.length - 1] : null; };
const inMask = (s) => { const m = lastMask(); return m ? m.querySelector(s) : null; };
const cardOf = (id) => doc.querySelector('.kb-card[data-ds="' + id + '"]');

let pass = 0; const fails = [];
const chk = (c, msg, d) => {
  if (c) { pass++; console.log('  \u2713 ' + msg); }
  else { fails.push(msg); console.log('  \u2717 ' + msg + (d !== undefined ? '  --> ' + d : '')); }
};

console.log('=== 归档到检索库 · 前端流程自检 ===');
window.KB.open();
await tick(60);
chk(!!cardOf(RETR_ID) && !!cardOf(UP_ID), '列表渲染出两个知识库');

console.log('\n【1】徽章与按钮显隐');
const retrCard = cardOf(RETR_ID), upCard = cardOf(UP_ID);
chk(!!retrCard.querySelector('.kb-state.retr'), '检索库卡片带「检索库」徽章');
chk(!upCard.querySelector('.kb-state.retr'), '非检索库卡片没有该徽章');
chk(!!upCard.querySelector('[data-act="arch"]'), '非检索库卡片有「归档到检索库」按钮');
chk(!retrCard.querySelector('[data-act="arch"]'), '检索库卡片不显示该按钮（避免自归档）');
chk(doc.querySelector('#kbCount').textContent.indexOf('检索库') >= 0,
  '计数条显示检索库数量', doc.querySelector('#kbCount').textContent);

console.log('\n【2】归档弹窗');
upCard.querySelector('[data-act="arch"]').click();
await tick(60);
chk(!!lastMask(), '归档弹窗已打开');
const sel = inMask('#kbaTarget');
chk(!!sel, '弹窗里有「目标检索库」下拉');
const opts = sel ? Array.from(sel.options).map((o) => o.value) : [];
chk(opts.length === 1 && opts[0] === RETR_ID, '下拉里只有检索库（且排除了自己）', JSON.stringify(opts));
chk(!!inMask('#kbaDel'), '有「归档后删除源文档」勾选框');

console.log('\n【3】确认后真的提交');
inMask('#kbaDel').checked = true;
calls.length = 0;
inMask('[data-act="ok"]').click();
await tick(120);
const posts = calls.filter((c) => c.indexOf('POST /api/kb/' + UP_ID + '/archive-to') === 0);
chk(posts.length === 1, '发出了库级归档请求', JSON.stringify(calls));
chk(!!posts[0] && posts[0].indexOf('"target":"' + RETR_ID + '"') > 0, '请求带上了目标检索库 id', posts[0]);
chk(!!posts[0] && posts[0].indexOf('"deleteSource":true') > 0, '请求带上了 deleteSource=true', posts[0]);

console.log('\n【4】归档完成回执');
const m = lastMask();
const txt = m ? m.textContent : '';
chk(!!m && /归档完成/.test(txt), '弹出「归档完成」回执');
chk(/内科1\.txt/.test(txt), '回执里写明归档到了哪个检索库', txt.slice(0, 80));
chk(/26 段/.test(txt) && /索引已完成/.test(txt), '回执里写明段数与索引状态', txt.slice(0, 160));
chk(/跳过 2 条未启用分段/.test(txt), '回执里提示跳过的未启用分段');
if (m) m.querySelector('[data-act="ok"]').click();
await tick(60);

console.log('\n【5】编辑弹窗里的「列入检索库」开关');
calls.length = 0;
const upCard2 = cardOf(UP_ID);          // 归档后列表已重渲染，这里要重新取一遍节点
chk(!!upCard2, '归档后列表已刷新，卡片仍在');
upCard2.querySelector('[data-act="edit"]').click();
await tick(60);
const chkBox = inMask('#kbeRetr');
chk(!!chkBox, '编辑弹窗里有「列入检索库」勾选框');
chk(chkBox && chkBox.checked === false, '当前库不在清单里 → 未勾选');
if (chkBox) chkBox.checked = true;
inMask('[data-act="ok"]').click();
await tick(120);
chk(calls.some((c) => c.indexOf('POST /api/kb/retrieval') === 0 && c.indexOf('"add":"' + UP_ID + '"') > 0),
  '勾选后调用了 /api/kb/retrieval 加入清单', JSON.stringify(calls));

console.log('');
console.log('通过 ' + pass + ' 项，失败 ' + fails.length + ' 项');
if (fails.length) { fails.forEach((f) => console.log('  \u2717 ' + f)); process.exit(1); }
console.log('\u2705 归档到检索库的前端流程可用');
