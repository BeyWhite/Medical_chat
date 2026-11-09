/* =========================================================================
   数据统计仪表盘（前端）
   展示当前登录用户在本机的对话历史统计：
     问答次数 · 高频问题 · 知识库命中率 · 来源分布 · 按天趋势
   接口：GET /api/stats?days=N  （本机后端聚合，数据都在本地 app.db）
   说明：纯原生实现，零第三方依赖；图表用 SVG / CSS 手绘，随深浅色主题自适应。
   ========================================================================= */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var LS_TOKEN = 'mq50_token';

  function token() { try { return localStorage.getItem(LS_TOKEN) || ''; } catch (e) { return ''; } }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function toast(msg, isErr) {
    var t = $('toast');
    if (!t) return;
    t.textContent = msg;
    t.className = 'toast on' + (isErr ? ' err' : '');
    clearTimeout(t._t);
    t._t = setTimeout(function () { t.className = 'toast'; }, 2800);
  }

  function fmtNum(n) {
    n = Number(n) || 0;
    if (n >= 10000) return (n / 10000).toFixed(n >= 100000 ? 0 : 1) + ' 万';
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  function fmtMs(ms) {
    if (ms == null) return '—';
    if (ms < 1000) return ms + ' ms';
    if (ms < 60000) return (ms / 1000).toFixed(1) + ' 秒';
    return (ms / 60000).toFixed(1) + ' 分钟';
  }

  var ICON = {
    chart: '<svg viewBox="0 0 24 24"><path d="M3 3v16a2 2 0 0 0 2 2h16"/><path d="M7 15l4-4 3 3 5-6"/></svg>',
    refresh: '<svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.3 6.4"/><path d="M20 4v7h-7"/></svg>',
    x: '<svg viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg>'
  };

  var st = {
    built: false,
    days: 30,
    data: null
  };

  /* ---------------- 请求 ---------------- */
  async function api(path) {
    var res = await fetch(path, { headers: { 'Authorization': 'Bearer ' + token() } });
    if (res.status === 401) { toast('登录状态已失效，请刷新页面重新登录', true); throw new Error('未登录'); }
    var data = {};
    try { data = await res.json(); } catch (e) {}
    if (!res.ok || data.ok === false) throw new Error(data.error || ('请求失败 ' + res.status));
    return data;
  }

  /* ---------------- 骨架 ---------------- */
  function build() {
    if (st.built) return;
    var drawer = $('statsDrawer');
    if (!drawer) return;
    drawer.innerHTML =
      '<div class="kb-head">' +
        '<h3><span class="kb-crumb">' +
          '<span class="kb-crumb-ic">' + ICON.chart + '</span>' +
          '<span class="kb-crumb-main">数据统计</span>' +
          '<span class="kb-crumb-sub" id="stRangeSub">近 30 天</span>' +
        '</span></h3>' +
        '<button class="icon-btn" id="statsRefresh" title="刷新">' + ICON.refresh + '</button>' +
        '<button class="icon-btn" id="statsClose" title="关闭">' + ICON.x + '</button>' +
      '</div>' +
      '<div class="kb-body st-body" id="statsBody"></div>';

    $('statsClose').addEventListener('click', close);
    $('statsRefresh').addEventListener('click', function () { load(); });
    $('statsBody').addEventListener('click', onBodyClick);
    st.built = true;
  }

  function open() {
    build();
    if (!token()) { toast('请先登录', true); return; }
    $('statsMask').classList.add('on');
    $('statsDrawer').classList.add('on');
    load();
  }
  function close() {
    $('statsMask').classList.remove('on');
    $('statsDrawer').classList.remove('on');
  }

  /* ---------------- 交互 ---------------- */
  function onBodyClick(e) {
    var el = e.target && e.target.closest ? e.target.closest('[data-days]') : null;
    if (el) {
      var d = parseInt(el.getAttribute('data-days'), 10);
      if (isNaN(d)) return;
      st.days = d;
      setRangeChips();
      load();
      return;
    }
    var q = e.target && e.target.closest ? e.target.closest('[data-q]') : null;
    if (q) {
      // 点击高频问题 → 填入输入框并聚焦，方便快速追问
      var input = $('q');
      if (input) { input.value = q.getAttribute('data-q'); input.focus(); }
      close();
    }
  }

  function setRangeChips() {
    var label = st.days === 0 ? '全部' : ('近 ' + st.days + ' 天');
    $('stRangeSub').textContent = label;
    Array.prototype.forEach.call(document.querySelectorAll('#statsBody [data-days]'), function (b) {
      b.classList.toggle('on', parseInt(b.getAttribute('data-days'), 10) === st.days);
    });
  }

  /* ---------------- 加载与渲染 ---------------- */
  async function load() {
    var body = $('statsBody');
    body.innerHTML =
      '<div class="kb-skel-wrap">' +
        '<div class="kb-skel-tip"><span class="spin"></span>统计中…</div>' +
        [0, 1, 2].map(function () {
          return '<div class="kb-skel">' +
            '<div class="skl-line w35"></div><div class="skl-line"></div><div class="skl-line w70"></div>' +
          '</div>';
        }).join('') +
      '</div>';
    var data;
    try {
      data = await api('/api/stats?days=' + st.days);
    } catch (err) {
      body.innerHTML = '<div class="st-empty">' + esc(err.message || '统计失败') + '</div>';
      return;
    }
    st.data = data;
    render(data);
    setRangeChips();
  }

  function render(d) {
    var body = $('statsBody');
    var t = d.totals || {};
    var src = d.sourceDist || [];
    var top = d.topQuestions || [];

    var rangeHtml =
      '<div class="st-range">' +
        [7, 30, 90, 0].map(function (n) {
          return '<button class="kb-tab' + (n === st.days ? ' on' : '') + '" data-days="' + n + '">' +
            (n === 0 ? '全部' : '近 ' + n + ' 天') + '</button>';
        }).join('') +
      '</div>';

    if (!t.questions && !src.length && !top.length) {
      body.innerHTML = rangeHtml +
        '<div class="st-empty">' +
          '<div class="st-empty-ic">' + ICON.chart + '</div>' +
          '<div>暂无统计数据</div>' +
          '<p>去问几个医学问题、或上传知识文件，回来就能看到趋势与命中率了。</p>' +
        '</div>';
      return;
    }

    var cardsHtml = renderCards(t, src, top);
    var ingestHtml = renderIngest(d.ingest);
    var trendHtml = renderTrend(d.trend || []);
    var sourceHtml = renderSource(src);
    var topHtml = renderTop(top);

    body.innerHTML = rangeHtml +
      cardsHtml +
      ingestHtml +
      trendHtml +
      sourceHtml +
      topHtml;
  }

  function renderCards(t, src, top) {
    var ratePct = Math.round((t.kbHitRate || 0) * 100);
    var cards = [
      {
        label: '问答次数', value: fmtNum(t.questions || 0),
        sub: '成功作答 ' + fmtNum(t.answered || 0) + ' · 失败 ' + fmtNum(t.errors || 0),
        cls: 'st-c-primary'
      },
      {
        label: '知识库命中率', value: ratePct + '%',
        sub: '命中 ' + fmtNum(t.kbHits || 0) + ' 次 / 作答 ' + fmtNum(t.answered || 0) + ' 次',
        cls: 'st-c-primary',
        ring: ratePct
      },
      {
        label: '平均响应耗时', value: fmtMs(t.avgLatencyMs),
        sub: '累计消耗 ' + fmtNum(t.totalTokens || 0) + ' tokens',
        cls: ''
      },
      {
        label: '覆盖知识库', value: fmtNum(src.length),
        sub: '高频问题去重 ' + fmtNum(top.length) + ' 条（Top 10）',
        cls: ''
      }
    ];
    return '<div class="st-cards">' + cards.map(function (c) {
      var ring = '';
      if (c.ring != null) {
        ring = '<span class="st-ring" style="--p:' + c.ring + '"><i>' + c.ring + '%</i></span>';
      }
      return '<div class="st-card ' + (c.cls || '') + '">' +
        '<div class="st-card-label">' + esc(c.label) + '</div>' +
        '<div class="st-card-row">' +
          (ring || '<span class="st-card-value">' + esc(c.value) + '</span>') +
        '</div>' +
        '<div class="st-card-sub">' + esc(c.sub) + '</div>' +
      '</div>';
    }).join('') + '</div>';
  }

  /* 知识入库：成功率环形 + 明细 */
  function renderIngest(ing) {
    ing = ing || {};
    var attempts = ing.attempts || 0;
    if (!attempts) return '';  // 无入库记录时不渲染该面板

    var success = ing.success || 0;
    var nonMed = ing.nonMedical || 0;
    var pct = ing.rate != null ? Math.round(ing.rate * 100) : 0;

    return '<div class="st-panel">' +
      '<div class="st-panel-head"><h4>知识入库</h4>' +
        '<span class="st-hint">上传文件 → 生成问答对 → 写入知识库</span>' +
      '</div>' +
      '<div class="st-ingest">' +
        '<div class="st-ingest-rate">' +
          '<span class="st-ring" style="--p:' + pct + '"><i>' + pct + '%</i></span>' +
          '<div class="st-ingest-rate-txt">' +
            '<div class="st-ingest-rate-num">' + pct + '%</div>' +
            '<div class="st-ingest-rate-lbl">入库成功率</div>' +
          '</div>' +
        '</div>' +
        '<div class="st-ingest-meta">' +
          '<div class="st-ingest-cell"><b>' + fmtNum(attempts) + '</b><span>入库次数</span></div>' +
          '<div class="st-ingest-cell ok"><b>' + fmtNum(success) + '</b><span>成功</span></div>' +
          '<div class="st-ingest-cell nm"><b>' + fmtNum(nonMed) + '</b><span>非医学拦截</span></div>' +
        '</div>' +
      '</div>' +
    '</div>';
  }

  /* 趋势折线：双系列（问答次数 / 命中次数），SVG 手绘，随主题色 */
  function renderTrend(trend) {
    var head = '<div class="st-panel"><div class="st-panel-head">' +
      '<h4>问答趋势</h4>' +
      '<div class="st-legend">' +
        '<span class="lg lg-q"></span>问答次数' +
        '<span class="lg lg-h"></span>知识库命中' +
      '</div>' +
    '</div>';

    if (!trend || !trend.length) {
      return head + '<div class="st-empty-sm">暂无趋势数据</div></div>';
    }

    var W = 860, H = 200, PL = 40, PR = 12, PT = 14, PB = 30;
    var iw = W - PL - PR, ih = H - PT - PB;
    var maxQ = 1, maxH = 1;
    trend.forEach(function (p) {
      if (p.questions > maxQ) maxQ = p.questions;
      if (p.hits > maxH) maxH = p.hits;
    });
    var maxY = Math.max(1, maxQ, maxH);
    // 上取整到整数，Y 轴刻度更整齐
    maxY = Math.ceil(maxY);

    var n = trend.length;
    var x = function (i) { return PL + (n === 1 ? iw / 2 : (i * iw / (n - 1))); };
    var y = function (v) { return PT + ih - (v / maxY) * ih; };

    var ptsQ = trend.map(function (p, i) { return x(i).toFixed(1) + ',' + y(p.questions).toFixed(1); }).join(' ');
    var ptsH = trend.map(function (p, i) { return x(i).toFixed(1) + ',' + y(p.hits).toFixed(1); }).join(' ');

    // 网格线（0 / 半 / 满）
    var grid = [0, 0.5, 1].map(function (f) {
      var gy = (PT + ih - f * ih).toFixed(1);
      return '<line x1="' + PL + '" y1="' + gy + '" x2="' + (W - PR) + '" y2="' + gy + '" class="st-grid"/>' +
        '<text x="' + (PL - 7) + '" y="' + (parseFloat(gy) + 4) + '" class="st-axis">' +
        Math.round(maxY * f) + '</text>';
    }).join('');

    // X 轴标签：点太多只显示首/中/尾
    var labelIdx = [0];
    if (n > 1) {
      if (n > 8) labelIdx.push(Math.floor((n - 1) / 2));
      labelIdx.push(n - 1);
    }
    var xlabels = labelIdx.map(function (i) {
      var p = trend[i];
      var d = p.day.slice(5); // MM-DD
      var anchor = i === 0 ? 'start' : (i === n - 1 ? 'end' : 'middle');
      return '<text x="' + x(i).toFixed(1) + '" y="' + (H - 8) + '" class="st-axis" text-anchor="' + anchor + '">' + d + '</text>';
    }).join('');

    var svg =
      '<svg class="st-svg" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMid meet">' +
        grid +
        xlabels +
        (n > 1
          ? '<polyline points="' + ptsQ + '" class="st-line st-line-q"/>' +
            '<polyline points="' + ptsH + '" class="st-line st-line-h"/>'
          : '<circle cx="' + x(0) + '" cy="' + y(trend[0].questions) + '" r="3.5" class="st-dot st-dot-q"/>' +
            '<circle cx="' + x(0) + '" cy="' + y(trend[0].hits) + '" r="3.5" class="st-dot st-dot-h"/>') +
      '</svg>';

    return head + svg + '</div>';
  }

  /* 来源分布：横向条形图 */
  function renderSource(src) {
    var head = '<div class="st-panel"><div class="st-panel-head"><h4>知识来源分布</h4></div>';
    if (!src || !src.length) {
      return head + '<div class="st-empty-sm">暂无知识来源数据</div></div>';
    }
    var max = src[0].count || 1;
    var rows = src.map(function (s) {
      var pct = Math.round((s.ratio || 0) * 100);
      var w = Math.max(2, Math.round((s.count / max) * 100));
      return '<div class="st-src-row">' +
        '<div class="st-src-name" title="' + esc(s.dataset) + '">' + esc(s.dataset) + '</div>' +
        '<div class="st-src-track"><div class="st-src-bar" style="width:' + w + '%"></div></div>' +
        '<div class="st-src-num">' + fmtNum(s.count) + ' 次 · ' + pct + '%</div>' +
      '</div>';
    }).join('');
    return head + '<div class="st-src">' + rows + '</div></div>';
  }

  /* 高频问题 Top 10 */
  function renderTop(top) {
    var head = '<div class="st-panel"><div class="st-panel-head">' +
      '<h4>高频问题 Top 10</h4>' +
      '<span class="st-hint">点击问题可填入输入框</span>' +
    '</div>';
    if (!top || !top.length) {
      return head + '<div class="st-empty-sm">暂无提问记录</div></div>';
    }
    var rows = top.map(function (q, i) {
      return '<button class="st-top-row" data-q="' + esc(q.text) + '">' +
        '<span class="st-top-rank' + (i < 3 ? ' hot' : '') + '">' + (i + 1) + '</span>' +
        '<span class="st-top-text" title="' + esc(q.text) + '">' + esc(q.text) + '</span>' +
        '<span class="st-top-count">' + fmtNum(q.count) + ' 次</span>' +
      '</button>';
    }).join('');
    return head + '<div class="st-top">' + rows + '</div></div>';
  }

  /* ---------------- 对外 ---------------- */
  window.STATS = { open: open, close: close };
  // 供 DOM 级自检脚本调用
  window.__stats = { api: api, render: render, state: st, esc: esc };
})();
