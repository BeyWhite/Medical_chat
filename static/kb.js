/* =========================================================================
   知识库管理（问答5.0 前端）
   三层导航：知识库列表 → 文档列表 → 分段列表
   能力：编辑（名称/描述/检索参数）、删除、禁用/启用、文档与分段的增删改
   接口：全部走本机后端 /api/kb*，由后端转发 Dify Service API（知识库密钥不下发浏览器）
   ========================================================================= */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var LS_TOKEN = 'mq50_token';

  /* ---------------- 基础工具 ---------------- */
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

  function fmtTime(ts) {
    if (!ts) return '—';
    var d = new Date(ts * 1000);
    if (isNaN(d.getTime())) return '—';
    var p = function (n) { return ('0' + n).slice(-2); };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  }

  function busy(btn, on, textWhile) {
    if (!btn) return;
    if (on) {
      btn._html = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<span class="sp"></span>' + (textWhile || '处理中');
    } else {
      btn.disabled = false;
      if (btn._html != null) btn.innerHTML = btn._html;
    }
  }

  var ICON = {
    kb: '<svg viewBox="0 0 24 24"><path d="M4 6.5A2.5 2.5 0 0 1 6.5 4H20v16H6.5A2.5 2.5 0 0 0 4 22.5"/><path d="M4 6.5v16"/></svg>',
    doc: '<svg viewBox="0 0 24 24"><path d="M13 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V8l-5-5Z"/><path d="M13 3v5h5"/></svg>',
    seg: '<svg viewBox="0 0 24 24"><path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.4" cy="6" r="1.4"/><circle cx="4.4" cy="12" r="1.4"/><circle cx="4.4" cy="18" r="1.4"/></svg>',
    edit: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17v3Z"/><path d="M14.5 6.5 17.5 9.5"/></svg>',
    trash: '<svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13"/></svg>',
    power: '<svg viewBox="0 0 24 24"><path d="M12 4v7"/><path d="M6.5 7a8 8 0 1 0 11 0"/></svg>',
    back: '<svg viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6"/></svg>',
    x: '<svg viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg>',
    search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/></svg>',
    arch: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8M10 12h4"/></svg>',
    refresh: '<svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.3 6.4"/><path d="M20 4v7h-7"/></svg>',
    info: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8v.1"/></svg>'
  };

  /* ---------------- 状态 ---------------- */
  var st = {
    view: 'ds',        // ds | docs | segs
    built: false,
    ds: { page: 1, limit: 20, total: 0, hasMore: false, keyword: '', items: [] },
    cur: null,         // 当前知识库
    docs: { page: 1, limit: 20, total: 0, hasMore: false, status: '', keyword: '', items: [] },
    curDoc: null,
    segs: { page: 1, limit: 10, total: 0, hasMore: false, keyword: '', items: [] }
  };

  /* ---------------- 请求 ---------------- */
  async function api(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
    opts.headers['Authorization'] = 'Bearer ' + token();
    var res = await fetch(path, opts);
    if (res.status === 401) { toast('登录状态已失效，请刷新页面重新登录', true); throw new Error('未登录'); }
    var data = {};
    try { data = await res.json(); } catch (e) {}
    if (!res.ok || data.ok === false) throw new Error(data.error || ('请求失败 ' + res.status));
    return data;
  }

  /* ---------------- 通用弹窗 ---------------- */
  function modal(opts) {
    return new Promise(function (resolve) {
      var mask = document.createElement('div');
      mask.className = 'kb-modal-mask';
      var need = !!opts.requireText;
      mask.innerHTML =
        '<div class="kb-modal' + (opts.danger ? ' danger' : '') + '">' +
          '<div class="kbm-head"><b>' + esc(opts.title || '') + '</b></div>' +
          '<div class="kbm-body">' +
            (opts.body || '') +
            (need ? '<label class="kbm-req">请输入知识库名称 <b>' + esc(opts.requireText) + '</b> 以确认' +
                      '<input type="text" id="kbmReq" placeholder="' + esc(opts.requireText) + '" autocomplete="off"></label>' : '') +
          '</div>' +
          '<div class="kbm-foot">' +
            '<button class="btn gho" data-act="cancel">取消</button>' +
            '<button class="btn ' + (opts.danger ? 'dgr' : 'pri') + '" data-act="ok"' + (need ? ' disabled' : '') + '>' +
              esc(opts.confirmText || '确定') + '</button>' +
          '</div>' +
        '</div>';
      document.body.appendChild(mask);
      requestAnimationFrame(function () { mask.classList.add('on'); });

      var input = mask.querySelector('#kbmReq');
      var okBtn = mask.querySelector('[data-act="ok"]');
      if (input) {
        input.addEventListener('input', function () {
          okBtn.disabled = input.value.trim() !== opts.requireText;
        });
      }
      function close(v) {
        mask.classList.remove('on');
        setTimeout(function () { if (mask.parentNode) mask.parentNode.removeChild(mask); }, 180);
        resolve(v);
      }
      mask.addEventListener('click', function (e) {
        var act = e.target && e.target.getAttribute && e.target.getAttribute('data-act');
        if (act === 'cancel' || e.target === mask) return close(false);
        if (act === 'ok') return close(true);
      });
      if (input) setTimeout(function () { input.focus(); }, 60);
    });
  }

  function confirmBox(title, body, confirmText, danger) {
    return modal({ title: title, body: '<p>' + body + '</p>', confirmText: confirmText || '确定', danger: !!danger });
  }

  /* ---------------- 状态徽章 ---------------- */
  var STATE_TEXT = {
    enabled: '已启用', partial: '部分禁用', disabled: '已禁用', empty: '空知识库', unknown: '状态未知'
  };
  function stateBadge(ds) {
    var t = STATE_TEXT[ds.state] || ds.state;
    return '<span class="kb-state ' + esc(ds.state) + '">' + esc(t) + '</span>';
  }

  /* ---------------- 骨架 ---------------- */
  function build() {
    if (st.built) return;
    var drawer = $('kbDrawer');
    if (!drawer) return;
    drawer.innerHTML =
      '<div class="kb-head">' +
        '<button class="kb-back icon-btn" id="kbBack" hidden title="返回">' + ICON.back + '</button>' +
        '<h3><span class="kb-crumb" id="kbCrumb">知识库管理</span></h3>' +
        '<button class="icon-btn" id="kbRefresh" title="刷新">' + ICON.refresh + '</button>' +
        '<button class="icon-btn" id="kbClose" title="关闭">' + ICON.x + '</button>' +
      '</div>' +
      '<div class="kb-toolbar" id="kbToolbar"></div>' +
      '<div class="kb-body" id="kbBody"></div>' +
      '<div class="kb-foot" id="kbFoot"></div>';

    $('kbClose').addEventListener('click', close);
    $('kbBack').addEventListener('click', function () {
      if (st.view === 'segs') { st.view = 'docs'; render(); }
      else if (st.view === 'docs') { st.view = 'ds'; render(); }
    });
    $('kbRefresh').addEventListener('click', function () {
      if (st.view === 'ds') loadDs();
      else if (st.view === 'docs') loadDocs();
      else loadSegs();
    });
    $('kbBody').addEventListener('click', onBodyClick);
    $('kbToolbar').addEventListener('click', onToolbarClick);
    $('kbToolbar').addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && e.target && e.target.id === 'kbKw') {
        st.ds.keyword = e.target.value.trim(); st.ds.page = 1; loadDs();
      }
    });
    $('kbFoot').addEventListener('click', onFootClick);
    st.built = true;
  }

  /* ---------------- 打开 / 关闭 ---------------- */
  function open() {
    build();
    if (!token()) { toast('请先登录', true); return; }
    $('kbMask').classList.add('on');
    $('kbDrawer').classList.add('on');
    st.view = 'ds';
    loadDs();
  }
  function close() {
    $('kbMask').classList.remove('on');
    $('kbDrawer').classList.remove('on');
  }

  /* ---------------- 渲染外壳 ---------------- */
  function render() {
    var back = $('kbBack');
    back.hidden = (st.view === 'ds');
    if (st.view === 'ds') {
      $('kbCrumb').textContent = '知识库管理';
      $('kbToolbar').innerHTML =
        '<div class="kb-search">' + ICON.search +
          '<input id="kbKw" type="text" placeholder="搜索知识库名称…" value="' + esc(st.ds.keyword) + '">' +
        '</div>' +
        '<button class="btn pri sm" id="kbSearchBtn">搜索</button>' +
        (st.ds.keyword ? '<button class="btn gho sm" id="kbClearBtn">清空</button>' : '') +
        '<span class="kb-count" id="kbCount"></span>';
      $('kbFoot').innerHTML = pagerHtml(st.ds);
    } else if (st.view === 'docs') {
      $('kbCrumb').textContent = (st.cur ? st.cur.name : '') + ' · 文档';
      $('kbToolbar').innerHTML =
        '<div class="kb-seg">' +
          ['', 'available', 'disabled', 'archived'].map(function (s) {
            var label = s === '' ? '全部' : (s === 'available' ? '可用' : (s === 'disabled' ? '已禁用' : '已归档'));
            return '<button class="kb-tab' + (st.docs.status === s ? ' on' : '') + '" data-docstatus="' + s + '">' + label + '</button>';
          }).join('') +
        '</div>' +
        '<span class="kb-count" id="kbCount"></span>';
      $('kbFoot').innerHTML = pagerHtml(st.docs);
    } else {
      $('kbCrumb').textContent = (st.curDoc ? st.curDoc.name : '') + ' · 分段';
      $('kbToolbar').innerHTML =
        '<div class="kb-search">' + ICON.search +
          '<input id="kbKw2" type="text" placeholder="搜索分段内容…" value="' + esc(st.segs.keyword) + '">' +
        '</div>' +
        '<button class="btn pri sm" id="kbSegSearch">搜索</button>' +
        (st.segs.keyword ? '<button class="btn gho sm" id="kbSegClear">清空</button>' : '') +
        '<span class="kb-count" id="kbCount"></span>';
      $('kbFoot').innerHTML = pagerHtml(st.segs);
    }
  }

  function pagerHtml(pg) {
    var pages = Math.max(1, Math.ceil((pg.total || 0) / pg.limit));
    if (!pg.total) return '';
    return '<span class="kb-pageinfo">第 ' + pg.page + ' / ' + pages + ' 页 · 共 ' + pg.total + ' 项</span>' +
      '<div class="kb-pager">' +
        '<button class="btn gho sm" data-pg="prev"' + (pg.page <= 1 ? ' disabled' : '') + '>上一页</button>' +
        '<button class="btn gho sm" data-pg="next"' + (pg.page >= pages ? ' disabled' : '') + '>下一页</button>' +
      '</div>';
  }

  function setCount(txt) { var el = $('kbCount'); if (el) el.textContent = txt || ''; }

  function loading(text) {
    $('kbBody').innerHTML = '<div class="kb-empty"><span class="sp"></span>' + esc(text || '加载中…') + '</div>';
  }
  function emptyBox(html) { $('kbBody').innerHTML = '<div class="kb-empty">' + html + '</div>'; }

  /* ---------------- ① 知识库列表 ---------------- */
  async function loadDs() {
    render(); loading('正在读取知识库列表…');
    try {
      var q = '/api/kb?page=' + st.ds.page + '&limit=' + st.ds.limit +
        (st.ds.keyword ? '&keyword=' + encodeURIComponent(st.ds.keyword) : '');
      var d = await api(q);
      st.ds.items = d.datasets || [];
      st.ds.total = d.total || 0;
      st.ds.hasMore = !!d.hasMore;
      render();
      renderDs();
    } catch (e) {
      emptyBox('<b>读取失败</b><p>' + esc(e.message) + '</p>');
    }
  }

  function renderDs() {
    if (!st.ds.items.length) {
      emptyBox(st.ds.keyword ? '没有匹配「' + esc(st.ds.keyword) + '」的知识库' : '还没有知识库。上传一份知识文件就会自动创建。');
      setCount('');
      return;
    }
    setCount('共 ' + st.ds.total + ' 个');
    $('kbBody').innerHTML = st.ds.items.map(function (d) {
      var rt = d.retrieval || {};
      var meta = [
        d.embeddingModel ? ('向量 ' + esc(d.embeddingModel)) : '关键词索引',
        rt.topK != null ? ('TopK ' + rt.topK) : '',
        rt.scoreThresholdEnabled ? ('阈值 ' + rt.scoreThreshold) : '不限阈值',
        rt.rerankingEnable ? 'Rerank' : ''
      ].filter(Boolean).join(' · ');

      var counts = [];
      if (d.enabledDocuments != null) counts.push('<i class="ok">可用 ' + d.enabledDocuments + '</i>');
      if (d.disabledDocuments) counts.push('<i class="dis">禁用 ' + d.disabledDocuments + '</i>');
      if (d.archivedDocuments) counts.push('<i class="arc">归档 ' + d.archivedDocuments + '</i>');

      var canEnable = d.state === 'disabled' || d.state === 'partial';
      var canDisable = d.state === 'enabled' || d.state === 'partial';

      return '<div class="kb-card" data-ds="' + esc(d.id) + '">' +
        '<div class="kbc-top">' +
          '<span class="kbc-ic">' + ICON.kb + '</span>' +
          '<div class="kbc-title">' +
            '<div class="kbc-name">' + esc(d.name) + stateBadge(d) + '</div>' +
            '<div class="kbc-desc">' + (d.description ? esc(d.description) : '<em class="dim">无描述</em>') + '</div>' +
          '</div>' +
          '<div class="kbc-nums">' +
            '<b>' + d.documentCount + '</b><span>文档</span>' +
            '<b>' + (d.wordCount || 0) + '</b><span>字数</span>' +
          '</div>' +
        '</div>' +
        '<div class="kbc-meta">' +
          '<span class="kbc-m">' + meta + '</span>' +
          (counts.length ? '<span class="kbc-cnt">' + counts.join('') + '</span>' : '') +
          '<span class="kbc-time">更新于 ' + fmtTime(d.updatedAt) + '</span>' +
        '</div>' +
        '<div class="kbc-acts">' +
          '<button class="btn gho sm" data-act="docs" data-id="' + esc(d.id) + '">' + ICON.doc + '文档管理</button>' +
          '<button class="btn gho sm" data-act="edit" data-id="' + esc(d.id) + '">' + ICON.edit + '编辑</button>' +
          '<button class="btn gho sm" data-act="' + (canEnable ? 'enable' : 'disable') + '" data-id="' + esc(d.id) + '"' +
            (canEnable || canDisable ? '' : ' disabled') + '>' + ICON.power + (canEnable ? '启用' : '禁用') + '</button>' +
          '<button class="btn gho sm dgr-txt" data-act="del" data-id="' + esc(d.id) + '">' + ICON.trash + '删除</button>' +
        '</div>' +
      '</div>';
    }).join('');
  }

  /* ---------------- ② 文档列表 ---------------- */
  async function loadDocs() {
    if (!st.cur) return;
    render(); loading('正在读取文档…');
    try {
      var q = '/api/kb/' + encodeURIComponent(st.cur.id) + '/documents?page=' + st.docs.page + '&limit=' + st.docs.limit;
      if (st.docs.status) q += '&status=' + st.docs.status;
      var d = await api(q);
      st.docs.items = d.documents || [];
      st.docs.total = d.total || 0;
      st.docs.hasMore = !!d.hasMore;
      render();
      renderDocs();
    } catch (e) {
      emptyBox('<b>读取失败</b><p>' + esc(e.message) + '</p>');
    }
  }

  function docStatusText(d) {
    if (d.archived) return '<span class="kb-state archived">已归档</span>';
    if (!d.enabled) return '<span class="kb-state disabled">已禁用</span>';
    if (d.indexingStatus && d.indexingStatus !== 'completed') return '<span class="kb-state partial">' + esc(d.indexingStatus) + '</span>';
    return '<span class="kb-state enabled">可用</span>';
  }

  function renderDocs() {
    if (!st.docs.items.length) {
      emptyBox('这里还没有文档。');
      setCount('共 ' + st.docs.total + ' 个');
      return;
    }
    setCount('共 ' + st.docs.total + ' 个');
    $('kbBody').innerHTML = st.docs.items.map(function (d) {
      return '<div class="kb-row" data-doc="' + esc(d.id) + '">' +
        '<span class="kbr-ic">' + ICON.doc + '</span>' +
        '<div class="kbr-main">' +
          '<div class="kbr-name">' + esc(d.name) + docStatusText(d) + '</div>' +
          '<div class="kbr-sub">' + (d.wordCount || 0) + ' 字 · ' + (d.tokens || 0) + ' tokens' +
            (d.hitCount ? ' · 命中 ' + d.hitCount + ' 次' : '') +
            ' · ' + fmtTime(d.createdAt) +
            (d.error ? ' · <em class="dgr-txt">' + esc(String(d.error).slice(0, 60)) + '</em>' : '') +
          '</div>' +
        '</div>' +
        '<div class="kbr-acts">' +
          '<button class="btn gho sm" data-act="segs" data-id="' + esc(d.id) + '">' + ICON.seg + '分段</button>' +
          '<button class="btn gho sm" data-act="' + (d.enabled ? 'dodisable' : 'doenable') + '" data-id="' + esc(d.id) + '"' +
            (d.archived ? ' disabled' : '') + '>' + ICON.power + (d.enabled ? '禁用' : '启用') + '</button>' +
          '<button class="btn gho sm" data-act="' + (d.archived ? 'unarchive' : 'archive') + '" data-id="' + esc(d.id) + '">' +
            ICON.arch + (d.archived ? '取消归档' : '归档') + '</button>' +
          '<button class="btn gho sm dgr-txt" data-act="docdel" data-id="' + esc(d.id) + '">' + ICON.trash + '删除</button>' +
        '</div>' +
      '</div>';
    }).join('');
  }

  /* ---------------- ③ 分段列表 ---------------- */
  async function loadSegs() {
    if (!st.cur || !st.curDoc) return;
    render(); loading('正在读取分段…');
    try {
      var q = '/api/kb/' + encodeURIComponent(st.cur.id) + '/documents/' + encodeURIComponent(st.curDoc.id) +
        '/segments?page=' + st.segs.page + '&limit=' + st.segs.limit;
      if (st.segs.keyword) q += '&keyword=' + encodeURIComponent(st.segs.keyword);
      var d = await api(q);
      st.segs.items = d.segments || [];
      st.segs.total = d.total || 0;
      st.segs.hasMore = !!d.hasMore;
      render();
      renderSegs();
    } catch (e) {
      emptyBox('<b>读取失败</b><p>' + esc(e.message) + '</p>');
    }
  }

  function renderSegs() {
    if (!st.segs.items.length) {
      emptyBox(st.segs.keyword ? '没有匹配的分段' : '这个文档还没有分段。');
      setCount('共 ' + st.segs.total + ' 段');
      return;
    }
    setCount('共 ' + st.segs.total + ' 段');
    $('kbBody').innerHTML = st.segs.items.map(function (s) {
      return '<div class="kb-seg-item' + (s.enabled ? '' : ' off') + '" data-seg="' + esc(s.id) + '">' +
        '<div class="kbs-head">' +
          '<span class="kbs-no">#' + (s.position != null ? s.position : '-') + '</span>' +
          (s.enabled ? '<span class="kb-state enabled">启用中</span>' : '<span class="kb-state disabled">已禁用</span>') +
          '<span class="kbs-wc">' + (s.wordCount || 0) + ' 字' + (s.hitCount ? ' · 命中 ' + s.hitCount : '') + '</span>' +
          '<div class="kbs-acts">' +
            '<button class="btn gho sm" data-act="segedit" data-id="' + esc(s.id) + '">' + ICON.edit + '编辑</button>' +
            '<button class="btn gho sm" data-act="' + (s.enabled ? 'segdisable' : 'segenable') + '" data-id="' + esc(s.id) + '">' +
              ICON.power + (s.enabled ? '禁用' : '启用') + '</button>' +
            '<button class="btn gho sm dgr-txt" data-act="segdel" data-id="' + esc(s.id) + '">' + ICON.trash + '删除</button>' +
          '</div>' +
        '</div>' +
        '<pre class="kbs-content">' + esc(s.content) + '</pre>' +
      '</div>';
    }).join('');
  }

  /* ---------------- 编辑知识库 ---------------- */
  function editDsDialog(d) {
    var rt = d.retrieval || {};
    var body =
      '<label class="kbm-fg"><span>名称</span>' +
        '<input type="text" id="kbeName" maxlength="40" value="' + esc(d.name) + '"></label>' +
      '<label class="kbm-fg"><span>描述</span>' +
        '<textarea id="kbeDesc" maxlength="400" rows="2" placeholder="可选">' + esc(d.description) + '</textarea></label>' +
      '<div class="kbm-grid">' +
        '<label class="kbm-fg"><span>Top K（召回条数）</span>' +
          '<input type="number" id="kbeTopK" min="1" max="20" value="' + (rt.topK != null ? rt.topK : 3) + '"></label>' +
        '<label class="kbm-fg"><span>相似度阈值</span>' +
          '<input type="number" id="kbeThr" min="0" max="1" step="0.01" value="' +
            (rt.scoreThreshold != null ? rt.scoreThreshold : 0.5) + '"></label>' +
      '</div>' +
      '<label class="kbm-chk"><input type="checkbox" id="kbeThrOn"' + (rt.scoreThresholdEnabled ? ' checked' : '') + '> 启用相似度阈值过滤</label>' +
      '<label class="kbm-chk"><input type="checkbox" id="kbeRr"' + (rt.rerankingEnable ? ' checked' : '') + '> 启用 Rerank 重排' +
        (rt.rerankingModel ? '<em class="dim">（' + esc(rt.rerankingModel) + '）</em>' : '') + '</label>' +
      '<p class="kbm-note">' + ICON.info + ' 检索方式为「' + esc(rt.searchMethod || 'semantic_search') +
        '」、向量模型 ' + esc(d.embeddingModel || '—') + ' 保持不变。改动只影响后续检索。</p>';

    return modal({ title: '编辑知识库', body: body, confirmText: '保存' }).then(function (ok) {
      if (!ok) return null;
      var name = $('kbeName').value.trim();
      if (!name) { toast('名称不能为空', true); return null; }
      return {
        name: name,
        description: $('kbeDesc').value.trim(),
        retrieval: {
          topK: parseInt($('kbeTopK').value, 10) || 3,
          scoreThresholdEnabled: $('kbeThrOn').checked,
          scoreThreshold: parseFloat($('kbeThr').value) || 0,
          rerankingEnable: $('kbeRr').checked
        }
      };
    });
  }

  async function saveDs(d, patch, btn) {
    busy(btn, true, '保存中');
    try {
      await api('/api/kb/' + encodeURIComponent(d.id), { method: 'PATCH', body: JSON.stringify(patch) });
      toast('已保存');
      await loadDs();
    } catch (e) {
      toast(e.message, true);
      busy(btn, false);
    }
  }

  /* ---------------- 事件：列表区 ---------------- */
  function onBodyClick(e) {
    var btn = e.target.closest ? e.target.closest('[data-act]') : null;
    if (btn) {
      var act = btn.getAttribute('data-act');
      var id = btn.getAttribute('data-id');
      var ds = st.ds.items.filter(function (x) { return x.id === id; })[0];
      if (act === 'edit' && ds) {
        editDsDialog(ds).then(function (patch) { if (patch) saveDs(ds, patch, btn); });
        return;
      }
      if (act === 'docs') { openDocs(ds); return; }
      if (act === 'enable' || act === 'disable') { toggleDs(ds, act === 'enable', btn); return; }
      if (act === 'del') { delDs(ds, btn); return; }

      // 文档级
      var doc = st.docs.items.filter(function (x) { return x.id === id; })[0];
      if (act === 'segs' && doc) { openSegs(doc); return; }
      if (act === 'doenable' || act === 'dodisable') { setDocEnabled(doc, act === 'doenable', btn); return; }
      if (act === 'archive' || act === 'unarchive') { setDocArchived(doc, act === 'archive', btn); return; }
      if (act === 'docdel') { delDoc(doc, btn); return; }

      // 分段级
      var seg = st.segs.items.filter(function (x) { return x.id === id; })[0];
      if (act === 'segedit' && seg) { editSeg(seg, btn); return; }
      if (act === 'segenable' || act === 'segdisable') { setSegEnabled(seg, act === 'segenable', btn); return; }
      if (act === 'segdel') { delSeg(seg, btn); return; }
      return;
    }
    // 整卡点击进入文档管理
    var card = e.target.closest ? e.target.closest('.kb-card') : null;
    if (card && st.view === 'ds') {
      var id2 = card.getAttribute('data-ds');
      var d2 = st.ds.items.filter(function (x) { return x.id === id2; })[0];
      if (d2) openDocs(d2);
    }
  }

  function onToolbarClick(e) {
    var t = e.target;
    if (t.id === 'kbSearchBtn') {
      var kw = $('kbKw');
      st.ds.keyword = kw ? kw.value.trim() : '';
      st.ds.page = 1; loadDs(); return;
    }
    if (t.id === 'kbClearBtn') { st.ds.keyword = ''; st.ds.page = 1; loadDs(); return; }
    if (t.id === 'kbSegSearch') {
      var k2 = $('kbKw2');
      st.segs.keyword = k2 ? k2.value.trim() : '';
      st.segs.page = 1; loadSegs(); return;
    }
    if (t.id === 'kbSegClear') { st.segs.keyword = ''; st.segs.page = 1; loadSegs(); return; }
    var tab = t.getAttribute && t.getAttribute('data-docstatus');
    if (tab !== null && tab !== undefined) {
      st.docs.status = tab; st.docs.page = 1; loadDocs(); return;
    }
  }

  function onFootClick(e) {
    var b = e.target.closest ? e.target.closest('[data-pg]') : null;
    if (!b || b.disabled) return;
    var dir = b.getAttribute('data-pg');
    if (st.view === 'ds') { st.ds.page = Math.max(1, st.ds.page + (dir === 'next' ? 1 : -1)); loadDs(); }
    else if (st.view === 'docs') { st.docs.page = Math.max(1, st.docs.page + (dir === 'next' ? 1 : -1)); loadDocs(); }
    else { st.segs.page = Math.max(1, st.segs.page + (dir === 'next' ? 1 : -1)); loadSegs(); }
  }

  /* ---------------- 进入下层 ---------------- */
  function openDocs(ds) {
    if (!ds) return;
    st.cur = ds;
    st.view = 'docs';
    st.docs = { page: 1, limit: 20, total: 0, hasMore: false, status: '', keyword: '', items: [] };
    loadDocs();
  }
  function openSegs(doc) {
    if (!doc) return;
    st.curDoc = doc;
    st.view = 'segs';
    st.segs = { page: 1, limit: 10, total: 0, hasMore: false, keyword: '', items: [] };
    loadSegs();
  }

  /* ---------------- 知识库：启停 / 删除 ---------------- */
  async function toggleDs(ds, enable, btn) {
    var other = enable ? '禁用' : '启用';
    var ok = await confirmBox(
      (enable ? '启用' : '禁用') + '知识库',
      (enable
        ? '将把《' + esc(ds.name) + '》里<strong>所有已禁用的文档</strong>重新启用，之后可被检索到。'
        : '将把《' + esc(ds.name) + '》里<strong>所有文档</strong>设为禁用，检索将<strong>不再命中</strong>这个知识库；需要时可以再启用。') +
      (ds.state === 'empty' ? '<br><em class="dim">注意：这个知识库目前没有文档。</em>' : ''),
      enable ? '确认启用' : '确认禁用', !enable
    );
    if (!ok) return;
    busy(btn, true, enable ? '启用中' : '禁用中');
    try {
      var d = await api('/api/kb/' + encodeURIComponent(ds.id) + '/toggle',
        { method: 'POST', body: JSON.stringify({ enabled: enable }) });
      toast(enable ? ('已启用（' + (d.affected || 0) + ' 个文档）') : ('已禁用（' + (d.affected || 0) + ' 个文档）'));
      await loadDs();
    } catch (e) {
      toast(e.message, true);
      busy(btn, false);
    }
  }

  async function delDs(ds, btn) {
    var ok = await modal({
      title: '删除知识库',
      danger: true,
      confirmText: '永久删除',
      requireText: ds.name,
      body: '<p>即将<strong>永久删除</strong>知识库《' + esc(ds.name) + '》及其中的 <strong>' + ds.documentCount +
        ' 个文档</strong>和全部向量数据。</p>' +
        '<p class="dgr-txt">此操作不可恢复。工作流里引用了这个知识库的话，需要重新同步数据集。</p>'
    });
    if (!ok) return;
    busy(btn, true, '删除中');
    try {
      await api('/api/kb/' + encodeURIComponent(ds.id), { method: 'DELETE' });
      toast('已删除');
      await loadDs();
    } catch (e) {
      toast(e.message, true);
      busy(btn, false);
    }
  }

  /* ---------------- 文档：启停 / 归档 / 删除 ---------------- */
  async function setDocEnabled(doc, enable, btn) {
    if (!doc) return;
    busy(btn, true);
    try {
      await api('/api/kb/' + encodeURIComponent(st.cur.id) + '/documents/' + encodeURIComponent(doc.id),
        { method: 'PATCH', body: JSON.stringify({ enabled: enable }) });
      toast(enable ? '已启用该文档' : '已禁用该文档（检索不再命中）');
      await loadDocs();
    } catch (e) { toast(e.message, true); busy(btn, false); }
  }

  async function setDocArchived(doc, archive, btn) {
    if (!doc) return;
    if (archive) {
      var ok = await confirmBox('归档文档',
        '归档后文档会被移出默认列表（可在「已归档」标签里找到），检索也不再命中。',
        '确认归档');
      if (!ok) return;
    }
    busy(btn, true);
    try {
      await api('/api/kb/' + encodeURIComponent(st.cur.id) + '/documents/' + encodeURIComponent(doc.id),
        { method: 'PATCH', body: JSON.stringify({ archived: archive }) });
      toast(archive ? '已归档' : '已取消归档');
      await loadDocs();
    } catch (e) { toast(e.message, true); busy(btn, false); }
  }

  async function delDoc(doc, btn) {
    if (!doc) return;
    var ok = await confirmBox('删除文档',
      '将<strong>永久删除</strong>文档《' + esc(doc.name) + '》及其全部分段与向量数据，不可恢复。',
      '永久删除', true);
    if (!ok) return;
    busy(btn, true, '删除中');
    try {
      await api('/api/kb/' + encodeURIComponent(st.cur.id) + '/documents/' + encodeURIComponent(doc.id),
        { method: 'DELETE' });
      toast('已删除文档');
      await loadDocs();
    } catch (e) { toast(e.message, true); busy(btn, false); }
  }

  /* ---------------- 分段：编辑 / 启停 / 删除 ---------------- */
  function editSeg(seg, btn) {
    var kws = seg.keywords || [];
    var body =
      '<label class="kbm-fg"><span>分段内容</span>' +
        '<textarea id="kseContent" rows="10" class="mono">' + esc(seg.content) + '</textarea></label>' +
      '<label class="kbm-fg"><span>关键词（逗号分隔，可留空）</span>' +
        '<input type="text" id="kseKw" value="' + esc(kws.join(', ')) + '" placeholder="例如：高原病, 吸氧">' +
        '<em class="kbm-tip">用于提升关键词检索的命中率；留空则不加关键词。</em></label>' +
      '<label class="kbm-chk"><input type="checkbox" id="kseOn"' + (seg.enabled ? ' checked' : '') + '> 启用这个分段</label>' +
      '<p class="kbm-note">' + ICON.info +
        ' 保存后 Dify 会<strong>重新为该分段建立索引</strong>，需要几秒钟；期间该文档的其它改动可能被暂时拒绝。' +
        (seg.enabled ? '' : '<br><strong>注意：</strong>这个分段当前是禁用状态，Dify 不允许修改已禁用的分段 —— ' +
          '请先勾选上面的「启用这个分段」。</p>');

    modal({ title: '编辑分段 #' + (seg.position != null ? seg.position : ''), body: body, confirmText: '保存并重建索引' })
      .then(function (ok) {
        if (!ok) return;
        var content = $('kseContent').value;
        if (!content.trim()) { toast('分段内容不能为空', true); return; }
        var kwRaw = $('kseKw').value;
        var keywords = kwRaw.split(/[,，\s]+/).filter(function (s) { return s; });
        saveSeg(seg, { content: content, keywords: keywords, enabled: $('kseOn').checked }, btn);
      });
  }

  async function saveSeg(seg, patch, btn) {
    busy(btn, true, '保存中');
    try {
      await api('/api/kb/' + encodeURIComponent(st.cur.id) + '/documents/' + encodeURIComponent(st.curDoc.id) +
        '/segments/' + encodeURIComponent(seg.id), { method: 'POST', body: JSON.stringify(patch) });
      toast('已保存，索引已重建');
      await loadSegs();
    } catch (e) { toast(e.message, true); busy(btn, false); }
  }

  async function setSegEnabled(seg, enable, btn) {
    busy(btn, true);
    try {
      await api('/api/kb/' + encodeURIComponent(st.cur.id) + '/documents/' + encodeURIComponent(st.curDoc.id) +
        '/segments/' + encodeURIComponent(seg.id),
        { method: 'POST', body: JSON.stringify({ enabled: enable }) });
      toast(enable ? '已启用该分段' : '已禁用该分段');
      await loadSegs();
    } catch (e) { toast(e.message, true); busy(btn, false); }
  }

  async function delSeg(seg, btn) {
    var ok = await confirmBox('删除分段',
      '将永久删除第 ' + (seg.position != null ? seg.position : '') + ' 个分段，不可恢复。', '永久删除', true);
    if (!ok) return;
    busy(btn, true, '删除中');
    try {
      await api('/api/kb/' + encodeURIComponent(st.cur.id) + '/documents/' + encodeURIComponent(st.curDoc.id) +
        '/segments/' + encodeURIComponent(seg.id), { method: 'DELETE' });
      toast('已删除分段');
      await loadSegs();
    } catch (e) { toast(e.message, true); busy(btn, false); }
  }

  /* ---------------- 对外 ---------------- */
  window.KB = { open: open, close: close };
  // 供 DOM 级自检脚本（verify_kb_ui.mjs）调用；不影响正常使用。
  window.__kb = { modal: modal, confirmBox: confirmBox, esc: esc, api: api, state: st, render: render };
})();
