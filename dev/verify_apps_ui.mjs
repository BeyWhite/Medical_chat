/* 「多套工作流连接」前端自检：下拉渲染 / 切换填充 / 新增 / 删除保护 / 保存请求体
   运行：JSDOM_PATH=C:/Users/ASUS/.workbuddy/binaries/node/workspace/node_modules/jsdom node dev/verify_apps_ui.mjs
*/
import fs from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { JSDOM } = require(process.env.JSDOM_PATH || 'jsdom');

const html = fs.readFileSync('static/index.html', 'utf8');
const appjs = fs.readFileSync('static/app.js', 'utf8');
const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://127.0.0.1:8001/' });
const { window } = dom, doc = window.document;
window.localStorage.setItem('mq50_token', 'tok');

const A1 = { id: 'default', name: '问答5.0', apiKeyMasked: 'app-wAlW…xt0z', hasKey: true,
  baseUrl: '', appType: 'chatflow', inputVar: '', extraInputs: {}, active: true };
const A2 = { id: 'b2c3d4e5f6', name: '智能问答02', apiKeyMasked: 'app-kHCK…vdVC', hasKey: true,
  baseUrl: 'http://192.168.1.9/v1', appType: 'chatflow', inputVar: '', extraInputs: {}, active: false };

let apps = [A1, A2];
let activeApp = 'default';
const calls = [];
const jb = (o, s = 200) => ({ ok: s < 400, status: s, json: async () => o, text: async () => JSON.stringify(o) });

window.fetch = async (url, opts = {}) => {
  const m = (opts.method || 'GET').toUpperCase(), u = String(url);
  calls.push({ m, u, body: opts.body ? JSON.parse(opts.body) : null });
  if (u === '/api/me') return jb({ ok: true, user: { id: 'u1', username: '_verify_apps_ui' } });
  if (u.startsWith('/api/conversations')) return jb({ ok: true, conversations: [] });
  if (u === '/api/health') return jb({ ok: true, configured: true, name: '问答5.0', mode: 'advanced-chat',
    appType: 'chatflow', activeApp, baseUrl: 'http://127.0.0.1/v1' });
  if (u === '/api/settings' && m === 'GET') return jb({ ok: true, baseUrl: A1.baseUrl || 'http://127.0.0.1/v1',
    appType: 'chatflow', inputVar: 'query', extraInputs: {}, stream: true, timeout: 900,
    apiKeyMasked: A1.apiKeyMasked, hasKey: true, datasetIdMasked: 'dataset-…n44O', hasDatasetKey: true,
    activeApp, activeName: '问答5.0', apps, globalBaseUrl: 'http://127.0.0.1/v1' });
  if (u === '/api/settings' && m === 'POST') return jb({ ok: true, saved: true, healthy: true,
    name: '问答5.0', activeApp, activeName: '问答5.0' });
  if (u === '/api/apps' && m === 'GET') return jb({ ok: true, activeApp, apps });
  if (u === '/api/apps' && m === 'POST') {
    const b = JSON.parse(opts.body);
    if (b.id) apps = apps.map((a) => (a.id === b.id ? Object.assign({}, a, { name: b.name, baseUrl: b.baseUrl }) : a));
    else apps = apps.concat([{ id: 'new123456', name: b.name, baseUrl: b.baseUrl, apiKeyMasked: 'app-new…xxxx',
      hasKey: true, appType: b.appType, inputVar: b.inputVar, extraInputs: {}, active: false }]);
    if (b.activate) { activeApp = b.id || 'new123456'; apps = apps.map((a) => Object.assign({}, a, { active: a.id === activeApp })); }
    return jb({ ok: true, created: !b.id, id: b.id || 'new123456', activeApp, apps });
  }
  if (u === '/api/apps/active' && m === 'POST') { activeApp = JSON.parse(opts.body).id; return jb({ ok: true, activeApp, apps }); }
  if (m === 'DELETE' && u.startsWith('/api/apps/')) {
    const id = u.slice('/api/apps/'.length);
    if (id === activeApp) return jb({ ok: false, error: '这是当前正在用的连接' }, 409);
    apps = apps.filter((a) => a.id !== id);
    return jb({ ok: true, activeApp, apps });
  }
  return jb({ ok: false, error: 'unmocked ' + m + ' ' + u }, 404);
};
if (!window.requestAnimationFrame) window.requestAnimationFrame = (cb) => setTimeout(cb, 0);
window.confirm = () => true;
window.eval(appjs);

const $ = (id) => doc.getElementById(id);
const tick = (ms = 5) => new Promise((r) => setTimeout(r, ms));
let pass = 0; const fails = [];
const chk = (c, msg, d) => { if (c) { pass++; console.log('  \u2713 ' + msg); } else { fails.push(msg); console.log('  \u2717 ' + msg + (d !== undefined ? '  --> ' + JSON.stringify(d) : '')); } };
const post = (u) => calls.filter((c) => c.m === 'POST' && c.u === u);
const del = (u) => calls.filter((c) => c.m === 'DELETE' && c.u === u);

await tick(30);
console.log('=== 接口设置：多套工作流连接 ===');

// 打开抽屉
$('btnSetting2').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
await tick(40);

const sel = $('cfgApp');
chk(!!sel, '抽屉里有「当前连接的工作流」下拉');
chk(sel.options.length === 2, '下拉渲染出 2 条连接（实际 ' + sel.options.length + '）');
chk(/问答5\.0/.test(sel.options[0].textContent) && /使用中/.test(sel.options[0].textContent),
  '第 1 项带名称与「使用中」标记：' + sel.options[0].textContent);
chk(sel.value === 'default', '默认选中当前启用的连接（' + sel.value + '）');
chk($('keyMasked').textContent === A1.apiKeyMasked, '密钥以掩码回显：' + $('keyMasked').textContent);
chk($('btnAppDel').disabled === false && /不能删除/.test($('btnAppDel').title),
  '「删除」保持可点，但 title 明说不能删（约定：可点 + 点击校验 + 明确提示）');

// 切换到第 2 条
sel.value = 'b2c3d4e5f6';
sel.dispatchEvent(new window.Event('change', { bubbles: true }));
await tick(10);
chk($('cfgAppName').value === '智能问答02', '切换后「连接名称」跟着变：' + $('cfgAppName').value);
chk($('cfgBase').value === 'http://192.168.1.9/v1', '切换后访问点跟着变：' + $('cfgBase').value);
chk($('keyMasked').textContent === A2.apiKeyMasked, '切换后密钥掩码跟着变');
chk(/删除这条连接/.test($('btnAppDel').title), '非当前连接可删除（title=「删除这条连接」）');

// 保存并重连
$('cfgKey').value = '';
$('btnSave').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
await tick(60);
const savePost = post('/api/apps').slice(-1)[0];
chk(!!savePost, '点了「保存并重连」会先 POST /api/apps');
chk(savePost && savePost.body.name === '智能问答02' && savePost.body.baseUrl === 'http://192.168.1.9/v1'
  && savePost.body.activate === true && savePost.body.id === 'b2c3d4e5f6',
  '请求体带上 id/名称/访问点/activate=true', savePost && savePost.body);
chk(!(savePost && savePost.body.apiKey), '密钥留空时不提交 apiKey（表示不修改）');
const setPost = post('/api/settings').slice(-1)[0];
chk(!!setPost && setPost.body.activeApp === 'b2c3d4e5f6', '随后 POST /api/settings 带上 activeApp', setPost && setPost.body);
chk(!('apiKey' in (setPost ? setPost.body : {})), '全局设置请求里不再重复提交 baseUrl/apiKey');
chk($('drawer').classList.contains('on') === false, '保存成功后抽屉自动关闭');

// 新增
$('btnSetting2').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
await tick(40);
$('btnAppNew').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
await tick(10);
chk($('cfgAppName').value === '', '「＋ 新增」清空名称输入框');
chk($('cfgApp').value === '__new__', '下拉切到「＋ 新连接（未保存）」');
const delN = calls.filter((c) => c.m === 'DELETE').length;
$('btnAppDel').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
await tick(15);
chk(calls.filter((c) => c.m === 'DELETE').length === delN && /先选一条已保存的连接/.test($('toast').textContent),
  '未保存的新连接点删除不发请求，提示：' + $('toast').textContent);
$('cfgAppName').value = '急救问答';
$('cfgBase').value = 'http://127.0.0.1/v1';
$('cfgKey').value = 'app-abcdefghijklmnop';
$('btnSave').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
await tick(60);
const newPost = post('/api/apps').slice(-1)[0];
chk(!!newPost && newPost.body.name === '急救问答' && newPost.body.apiKey === 'app-abcdefghijklmnop'
  && !('id' in newPost.body), '新增时提交名称与 App Key、不带 id', newPost && newPost.body);

// 删除保护：当前使用的连接不能被删（后端 409 → 前端提示）
const fb = [];
const oldToast = $('toast');
$('btnSetting2').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
await tick(40);
$('cfgApp').value = activeApp;
$('cfgApp').dispatchEvent(new window.Event('change', { bubbles: true }));
$('btnAppDel').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
await tick(20);
chk(del('/api/apps/' + activeApp).length === 0, '对「正在使用」的连接不会发出 DELETE');
chk(/先切到别的连接/.test(oldToast.textContent), '给出明确提示：' + oldToast.textContent);

// 删除非当前连接
$('cfgApp').value = 'b2c3d4e5f6';
$('cfgApp').dispatchEvent(new window.Event('change', { bubbles: true }));
await tick(10);
$('btnAppDel').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
await tick(40);
chk(del('/api/apps/b2c3d4e5f6').length === 1, '删除非当前连接会发出 DELETE');
chk($('cfgApp').options.length === 2, '删除后下拉刷新（剩下默认 + 新增的 2 条）');

console.log('---');
console.log('通过 ' + pass + ' 项，失败 ' + fails.length + ' 项');
if (fails.length) { fails.forEach((f) => console.log('  \u2717 ' + f)); process.exit(1); }
console.log('\u2705 全部通过');
