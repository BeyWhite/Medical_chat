/* 「数据统计仪表盘」渲染自检（jsdom）
   覆盖 stats.js 的 render() 输出：
     ① 指标卡（4 张）、命中率 / 次数等关键数值
     ② 趋势折线（双系列）、单点退化圆点
     ③ 来源分布条形、高频问题列表（可点击带回问题）
     ④ 时间范围切换按钮、空数据兜底
   运行：JSDOM_PATH=<jsdom> node dev/verify_stats_ui.mjs
*/
import fs from 'node:fs';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { JSDOM } = require(process.env.JSDOM_PATH || 'jsdom');

const html = fs.readFileSync('static/index.html', 'utf8');
const statsjs = fs.readFileSync('static/stats.js', 'utf8');

const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://127.0.0.1:8001/' });
const { window } = dom, doc = window.document;
window.localStorage.setItem('mq50_token', 'tok');
window.eval(statsjs);

// 模拟 build() 的产物：给 statsDrawer 塞入 statsBody 容器
doc.getElementById('statsDrawer').innerHTML = '<div class="kb-body st-body" id="statsBody"></div>';
const body = doc.getElementById('statsBody');

let pass = 0; const fails = [];
const chk = (c, m) => { if (c) { pass++; console.log('  \u2713 ' + m); } else { fails.push(m); console.log('  \u2717 ' + m); } };

const DATA = {
  totals: { questions: 73, answered: 67, errors: 0, kbHits: 53, kbHitRate: 0.791, avgLatencyMs: 63739, totalTokens: 218096 },
  trend: [
    { day: '2026-10-12', questions: 8, hits: 5 },
    { day: '2026-10-13', questions: 40, hits: 29 },
    { day: '2026-10-14', questions: 20, hits: 14 },
    { day: '2026-10-15', questions: 5, hits: 5 }
  ],
  sourceDist: [
    { dataset: '新增医学知识检索库.txt', count: 207, ratio: 0.8734 },
    { dataset: '妇产科1.md', count: 18, ratio: 0.0759 }
  ],
  topQuestions: [
    { text: '中暑的救治措施有哪些', count: 9 },
    { text: '院前急救的原则有哪些', count: 8 }
  ],
  ingest: { attempts: 20, success: 19, nonMedical: 1, errors: 0, skipped: 0, rate: 0.95 }
};

console.log('=== stats.js 渲染自检 ===');
let threw = null;
try { window.__stats.render(DATA); } catch (e) { threw = e; }
chk(threw === null, 'render() 不抛异常' + (threw ? ' -> ' + threw.message : ''));

const out = body.innerHTML;
chk(out.includes('st-cards'), '产出指标卡容器');
chk((out.match(/st-card\b/g) || []).length >= 4, '至少 4 张指标卡');
chk(out.includes('79%'), '命中率 79% 正确显示');
chk(out.includes('73'), '问答次数 73 正确显示');
chk(out.includes('st-svg'), '产出趋势折线图');
chk(out.includes('st-line-q') && out.includes('st-line-h'), '双系列折线（问答 + 命中）');
chk(out.includes('st-src'), '产出来源分布');
chk(out.includes('新增医学知识检索库.txt'), '来源分布包含知识库名');
chk(out.includes('st-top'), '产出高频问题列表');
chk(out.includes('中暑的救治措施有哪些'), '高频问题含具体文本');
chk(out.includes('data-q='), '高频问题可点击（带 data-q）');
chk(out.includes('data-days'), '时间范围切换按钮存在');
chk(out.includes('st-ingest'), '产出知识入库面板');
chk(out.includes('入库成功率'), '显示入库成功率');
chk(out.includes('非医学拦截'), '显示非医学拦截量');
chk(!out.includes('失败·跳过'), '已移除「失败·跳过」格');
chk(!out.includes('st-ingest-bar'), '已移除三段堆叠条');

// 空数据分支
window.__stats.render({ totals: { questions: 0 }, sourceDist: [], topQuestions: [] });
chk(body.innerHTML.includes('st-empty'), '空数据产出空状态提示');

// 单点趋势分支（n=1）
window.__stats.render({ totals: { questions: 1, kbHitRate: 0 }, sourceDist: [], topQuestions: [], trend: [{ day: '2026-10-15', questions: 1, hits: 1 }] });
chk(body.innerHTML.includes('st-dot'), '单点趋势用圆点而非折线');

console.log('\n通过 ' + pass + ' / ' + (pass + fails.length));
if (fails.length) { console.log('失败项：\n' + fails.map((f) => '  - ' + f).join('\n')); process.exit(1); }
