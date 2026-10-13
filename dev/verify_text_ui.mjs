/* 「文案防线」自检（纯 node，不需要 jsdom）
   背景：工作流时长随时会被人优化 / 变快变慢，界面与文档里写死的「实测 3~4 分钟」这类
   承诺话术会立刻过期、误导使用者。2026-10-13 已按用户要求整体删除耗时提示。
   本脚本锁死两件事：
     ① 这些过期话术不许再回来（前端 + 两份文档都查）；
     ② 每次回答真实测量的元信息（耗时 / 首字 / tokens）等**非承诺性**文案不许被误删。
   运行：node dev/verify_text_ui.mjs
*/
import fs from 'node:fs';

const read = (p) => fs.readFileSync(p, 'utf8');
const JS = read('static/app.js');
const HTML = read('static/index.html');
const KB = read('static/kb.js');
const README = read('README.md');
const HOW = read('如何打开.md');

let pass = 0; const fails = [];
const chk = (c, msg, d) => { if (c) { pass++; console.log('  \u2713 ' + msg); } else { fails.push(msg); console.log('  \u2717 ' + msg + (d !== undefined ? '  --> ' + JSON.stringify(d) : '')); } };

/* 过期话术（整句级，避免误伤「自检脚本运行约 8~10 分钟」这类与工作流无关的说明） */
const STALE = [
  ['3~4 分钟', '问答分支的耗时承诺'],
  ['1~2 分钟', '文件入库分支的耗时承诺'],
  ['实测整体约', '「实测整体约…」话术'],
  ['耗时约', '「耗时约…」话术'],
  ['首字延迟约 17 秒', '流式首字延迟承诺'],
  ['25–70 秒', '完整应答时长承诺'],
];
/* 只在这些文件里查；README 的自检脚本耗时属于“工具运行时长”，是客观事实，不受影响 */
const files = { 'static/app.js': JS, 'static/index.html': HTML, 'static/kb.js': KB, 'README.md': README, '如何打开.md': HOW };

console.log('=== 文案防线：界面 / 文档里不许再出现工作流耗时承诺 ===');
for (const [needle, label] of STALE) {
  const hit = Object.keys(files).filter((f) => files[f].includes(needle));
  chk(hit.length === 0, '① 全站已无 ' + label + '（「' + needle + '」）', hit);
}

console.log('--- ② 欢迎页：耗时提示卡整块删除 ---');
const welcome = JS.slice(JS.indexOf("'<div class=\"welcome\">'"), JS.indexOf("Array.prototype.forEach.call($('thread').querySelectorAll('.card')"));
chk(welcome.length > 0, '② 定位到欢迎页模板');
chk(!welcome.includes('耗时提示'), '② 欢迎页不再有「耗时提示」卡片');
chk(welcome.includes('免责声明'), '② 免责声明卡片保留（只删了耗时那张）');
chk((welcome.match(/class="notice"/g) || []).length === 1, '② 欢迎页只剩 1 张 notice（原为 2 张）',
  (welcome.match(/class="notice"/g) || []).length);
chk(/约\s*\d+\s*[~–-]\s*\d+\s*分钟/.test(welcome) === false, '② 欢迎页正文不含任何「约 N~N 分钟」');

console.log('--- ③ 等待中的占位文案 ---');
chk(JS.includes('正在生成 5 个相似问、逐条检索知识库，再汇总作答…'), '③ 问答分支占位文案保留');
chk(JS.includes('正在提取文件正文并生成问答对，写入知识库…'), '③ 文件入库占位文案保留');
const think = JS.slice(JS.indexOf("bub.innerHTML = '<div class=\"think\">"), JS.indexOf('setBusy(true)'));
chk(think.length > 0 && !/分钟/.test(think), '③ 两句占位文案都不再带「（约 … 分钟）」', /分钟/.test(think));

console.log('--- ④ 接口设置里的说明 ---');
chk(!/实测[^<>]{0,20}秒/.test(HTML), '④ 流式输出说明不再写实测延迟秒数');
chk(/id="cfgStream"/.test(HTML) && HTML.includes('开启后逐字返回'), '④ 流式输出说明改为行为描述（保留勾选项）');
chk(HTML.includes('耗时较长，建议不小于 600 秒'), '④ 请求超时说明保留「建议不小于 600 秒」（这是使用建议，不是耗时承诺）');
chk(!HTML.includes('耗时约 3~4 分钟'), '④ 底部「两条分支」说明已去掉耗时');

console.log('--- ⑤ 真实数据类文案不许被误删 ---');
chk(JS.includes("'首字 '"), '⑤ 每条回答的「首字 xx s」真实测量值保留');
chk(JS.includes('info.latencyMs'), '⑤ 每条回答的耗时芯片保留');
chk(JS.includes('tokens</span>'), '⑤ tokens 芯片保留');
chk(JS.includes('10–20 秒'), '⑤ 限流重试提示（10–20 秒）保留 —— 与工作流耗时无关');
chk(KB.includes('10~60 秒'), '⑤ 归档建索引等待提示保留 —— 与工作流耗时无关');

console.log('--- ⑥ 文档 ---');
chk(README.includes('| 分支 | 触发条件 | 工作流做什么 |'), '⑥ README 分支表已去掉「实测耗时」列');
chk(!/\|\s*约\s*\*\*\d+~\d+ 分钟\*\*\s*\|/.test(README), '⑥ README 分支表里没有耗时单元格');
chk(README.includes('等待时间偏长属正常'), '⑥ README FAQ「答得很慢」改为不承诺具体时长');
chk(HOW.includes('等待期间有转圈动画，可随时点「停止」'), '⑥ 如何打开.md 问答案例改为不承诺时长');
chk(!HOW.includes('实测 3~4 分钟'), '⑥ 如何打开.md 排错表已去掉实测时长');

console.log('\n结果：' + pass + ' 通过 / ' + fails.length + ' 失败');
if (fails.length) { fails.forEach((f) => console.log('  - ' + f)); process.exit(1); }
