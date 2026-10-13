/* =========================================================================
   知识库管理（问答5.0 前端）
   三层导航：知识库列表 → 文档列表 → 分段列表
   能力：编辑（名称/描述/检索参数）、删除、禁用/启用、文档与分段的增删改
   接口：全部走本机后端 /api/kb*，由后端转发 Dify Service API（知识库密钥不下发浏览器）

   ⚠️ 按钮配色规范（本抽屉内只有三色，别再引入第四种）：
     ① 主操作  = `btn pri sm`      品牌蓝实心：进下一层（文档管理 / 分段）、新增分段
     ② 次要操作 = `btn gho sm`      灰色描边：编辑、启用/禁用、归档、搜索、清空、翻页、取消
     ③ 破坏性  = `btn gho sm dgr-txt` 橙色：删除（弹窗里的确认按钮用 `btn dgr` 实心橙）
   不要再用 `pri-txt`（青色描边）：它只是"接口设置"里给新增连接留的样式。
   详见 README「按钮配色」一节与 dev/verify_btn_ui.mjs。
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

  /* 大数字更好读：12345 → 1.2万 */
  function fmtNum(n) {
    n = Number(n) || 0;
    if (n >= 10000) return (n / 10000).toFixed(n >= 100000 ? 0 : 1) + ' 万';
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
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
    // 归档到检索库：方框 + 向右的入箭头
    retr: '<svg viewBox="0 0 24 24"><path d="M4 5h6v14H4z"/><path d="M14 9h6M17 6l3 3-3 3"/></svg>',
    refresh: '<svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.3 6.4"/><path d="M20 4v7h-7"/></svg>',
    info: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8v.1"/></svg>',
    plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
    inbox: '<svg viewBox="0 0 24 24"><path d="M3 13h4l1.5 2.5h7L17 13h4"/><path d="M5.5 5h13l2.5 8v5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-5L5.5 5Z"/></svg>',
    layers: '<svg viewBox="0 0 24 24"><path d="m12 3 8.5 4.6L12 12.2 3.5 7.6 12 3Z"/><path d="m3.5 12.4 8.5 4.6 8.5-4.6"/></svg>'
  };

  /* ---------------- 状态 ---------------- */
  var st = {
    view: 'ds',        // ds | docs | segs
    built: false,
    ds: { page: 1, limit: 20, total: 0, hasMore: false, keyword: '', items: [] },
    cur: null,         // 当前知识库
    docs: { page: 1, limit: 20, total: 0, hasMore: false, status: '', keyword: '', items: [] },
    curDoc: null,
    segs: { page: 1, limit: 10, total: 0, hasMore: false, keyword: '', items: [] },
    // 「检索库」：Dify 工作流「知识检索」节点覆盖到的知识库
    retrieval: { loaded: false, ids: [], items: [] }
  };

  function isRetrieval(id) {
    return st.retrieval.ids.indexOf(id) >= 0;
  }
  function retrievalTitle() {
    return (st.retrieval.items || []).map(function (x) {
      return x.exists ? x.name : (x.id.slice(0, 8) + '…（已失效）');
    }).join('、');
  }

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
      // 同一时刻只允许一个弹窗：旧弹窗（含刚关闭、还在等 180ms 移除的）立即清掉，
      // 否则两个弹窗会各带一份 #ksnContent / #kbeName 等固定 id，getElementById 读串台。
      Array.prototype.forEach.call(document.querySelectorAll('.kb-modal-mask'), function (m) {
        if (m.parentNode) m.parentNode.removeChild(m);
      });
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

  /* 纯展示型弹窗（不返回 Promise），用于「正在归档…」这类长任务，
     任务结束时由调用方 close()，期间用户点「隐藏」也能先收起。 */
  function busyModal(title, html) {
    var mask = document.createElement('div');
    mask.className = 'kb-modal-mask';
    mask.innerHTML =
      '<div class="kb-modal">' +
        '<div class="kbm-head"><b>' + esc(title) + '</b></div>' +
        '<div class="kbm-body">' + html + '</div>' +
        '<div class="kbm-foot"><button class="btn gho" data-act="hide">隐藏</button></div>' +
      '</div>';
    document.body.appendChild(mask);
    requestAnimationFrame(function () { mask.classList.add('on'); });
    function close() {
      mask.classList.remove('on');
      setTimeout(function () { if (mask.parentNode) mask.parentNode.removeChild(mask); }, 180);
    }
    mask.addEventListener('click', function (e) {
      var act = e.target && e.target.getAttribute && e.target.getAttribute('data-act');
      if (act === 'hide' || e.target === mask) close();
    });
    return { close: close };
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
    $('kbBack').addEventListener('click', goBack);
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
    loadRetrieval();
    loadDs();
  }
  function close() {
    $('kbMask').classList.remove('on');
    $('kbDrawer').classList.remove('on');
  }

  /* 返回上一级（分段 → 文档 → 知识库）
     注意：必须真正重拉数据，让 #kbBody 重绘；
     只调 render() 的话面包屑换了、正文不动，用户看到的就是「点了没反应」。 */
  function goBack() {
    if (st.view === 'segs') {
      st.view = 'docs';
      if (st.cur) loadDocs();
      else { st.view = 'ds'; loadDs(); }   // 父级丢了就一路退回根
      return;
    }
    if (st.view === 'docs') {
      st.view = 'ds';
      loadDs();
    }
    // ds 视图没有上一级（此时按钮本身也是隐藏的）
  }

  /* ---------------- 渲染外壳 ---------------- */
  function crumbHtml(main, sub) {
    return '<span class="kb-crumb-ic">' + ICON.layers + '</span>' +
      '<span class="kb-crumb-main" title="' + esc(main) + '">' + esc(main) + '</span>' +
      (sub ? '<span class="kb-crumb-sub">' + esc(sub) + '</span>' : '');
  }

  function render() {
    var back = $('kbBack');
    back.hidden = (st.view === 'ds');
    back.title = st.view === 'segs' ? '返回文档列表' : (st.view === 'docs' ? '返回知识库列表' : '返回');
    back.setAttribute('aria-label', back.title);
    if (st.view === 'ds') {
      $('kbCrumb').innerHTML = crumbHtml('知识库管理', '');
      $('kbToolbar').innerHTML =
        '<div class="kb-search">' + ICON.search +
          '<input id="kbKw" type="text" placeholder="搜索知识库名称…" value="' + esc(st.ds.keyword) + '">' +
        '</div>' +
        '<button class="btn gho sm" id="kbSearchBtn">搜索</button>' +
        (st.ds.keyword ? '<button class="btn gho sm" id="kbClearBtn">清空</button>' : '') +
        '<span class="kb-count" id="kbCount"></span>';
      $('kbFoot').innerHTML = pagerHtml(st.ds);
    } else if (st.view === 'docs') {
      $('kbCrumb').innerHTML = crumbHtml(st.cur ? st.cur.name : '', '文档');
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
      $('kbCrumb').innerHTML = crumbHtml(st.curDoc ? st.curDoc.name : '', '分段');
      $('kbToolbar').innerHTML =
        '<button class="btn pri sm" id="kbSegAdd" title="在这个文档里新增一个分段">' + ICON.plus + '新增分段</button>' +
        '<div class="kb-search">' + ICON.search +
          '<input id="kbKw2" type="text" placeholder="搜索分段内容…" value="' + esc(st.segs.keyword) + '">' +
        '</div>' +
        '<button class="btn gho sm" id="kbSegSearch">搜索</button>' +
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
    $('kbBody').innerHTML =
      '<div class="kb-skel-wrap">' +
        '<div class="kb-skel-tip"><span class="spin"></span>' + esc(text || '加载中…') + '</div>' +
        [0, 1, 2].map(function () {
          return '<div class="kb-skel">' +
            '<div class="skl-line w35"></div><div class="skl-line"></div><div class="skl-line w70"></div>' +
          '</div>';
        }).join('') +
      '</div>';
  }

  /* 空态：图标 + 标题 + 说明（+ 可选操作），比一行灰字更好认 */
  function emptyBox(icon, title, text, extra) {
    $('kbBody').innerHTML =
      '<div class="kb-empty">' +
        '<div class="kbe-ic">' + (icon || ICON.inbox) + '</div>' +
        '<b>' + (title || '') + '</b>' +
        (text ? '<p>' + text + '</p>' : '') +
        (extra || '') +
      '</div>';
  }
  function errorBox(msg) { emptyBox(ICON.inbox, '读取失败', esc(msg), ''); }

  /* ---------------- 检索库清单 ---------------- */
  async function loadRetrieval() {
    try {
      var d = await api('/api/kb/retrieval');
      var r = d.retrieval || {};
      st.retrieval.ids = r.ids || [];
      st.retrieval.items = r.datasets || [];
      st.retrieval.loaded = true;
    } catch (e) {
      st.retrieval.loaded = true;   // 失败也标记，避免反复重试
      st.retrieval.ids = [];
      st.retrieval.items = [];
    }
    if (st.view === 'ds' && st.ds.items.length) { renderDs(); }
  }

  async function setRetrieval(dsId, on, btn) {
    busy(btn, true);
    try {
      var d = await api('/api/kb/retrieval', {
        method: 'POST',
        body: JSON.stringify(on ? { add: dsId } : { remove: dsId })
      });
      var r = d.retrieval || {};
      st.retrieval.ids = r.ids || [];
      st.retrieval.items = r.datasets || [];
      st.retrieval.loaded = true;
      toast(on ? '已列入检索库：新建对话就能检索到它' : '已移出检索清单');
      renderDs();
    } catch (e) { toast(e.message, true); }
    finally { busy(btn, false); }
  }

  /* ---------------- 归档到检索库 ---------------- */
  function targetOptions(excludeId) {
    return (st.retrieval.items || []).filter(function (x) {
      return x.id !== excludeId;
    }).map(function (x) {
      var label = (x.exists ? x.name : x.id.slice(0, 8) + '…') +
        (x.exists ? '（' + (x.documentCount || 0) + ' 篇文档）' : '（已失效）');
      return '<option value="' + esc(x.id) + '"' + (x.exists ? '' : ' disabled') + '>' + esc(label) + '</option>';
    }).join('');
  }

  async function archDialog(opt) {
    // opt: { mode: 'ds' | 'doc', ds, doc }
    if (!st.retrieval.loaded) await loadRetrieval();
    var srcName = opt.mode === 'doc' ? (opt.doc ? opt.doc.name : '') : (opt.ds ? opt.ds.name : '');
    var usable = (st.retrieval.items || []).filter(function (x) {
      return x.exists && x.id !== (opt.ds ? opt.ds.id : '');
    });
    if (!usable.length) {
      await modal({
        title: '没有可用的检索库',
        body: '<p>「检索库」是 Dify 工作流里<strong>知识检索节点覆盖到的知识库</strong>，' +
          '只有归档到这些库里，新建对话才检索得到。</p>' +
          '<p>当前清单：' + (st.retrieval.items.length
            ? esc(retrievalTitle())
            : '<em class="dim">（空）</em>') + '</p>' +
          '<p class="kbm-note">' + ICON.info + ' 请在知识库列表里选一个库，点「编辑」勾选「列入检索库」，' +
          '或直接点卡片上的「设为检索库」。</p>',
        confirmText: '知道了'
      });
      return;
    }

    var body =
      '<p>' + (opt.mode === 'doc' ? '把文档 ' : '把知识库 ') +
        '<strong>' + esc(srcName) + '</strong>' +
        (opt.mode === 'doc' ? '' : ' 里<strong>全部文档</strong>') +
        ' 的内容归档（复制）进一个检索库，之后新建对话即可检索到。</p>' +
      '<label class="kbm-fg"><span>目标检索库</span>' +
        '<select id="kbaTarget">' + targetOptions(opt.ds ? opt.ds.id : '') + '</select>' +
        '<em class="kbm-tip">只有清单里的库会被工作流检索到。</em></label>' +
      '<label class="kbm-chk"><input type="checkbox" id="kbaDel"> 归档后删除源' +
        (opt.mode === 'doc' ? '文档' : '文档（源知识库保留）') + '（搬走，而不是复制）</label>' +
      '<p class="kbm-note">' + ICON.info +
        ' 只搬「启用中」的分段；目标库里<strong>同名文档会被覆盖更新</strong>，所以重复归档是安全的。' +
        '归档后 Dify 要重建索引，可能要 10~60 秒。</p>';

    var ok = await modal({
      title: '归档到检索库',
      body: body,
      confirmText: '开始归档'
    });
    if (!ok) return;

    var target = $('kbaTarget').value;
    if (!target) { toast('请选择目标检索库', true); return; }
    var delSrc = $('kbaDel').checked;

    var url = opt.mode === 'doc'
      ? '/api/kb/' + encodeURIComponent(opt.ds.id) + '/documents/' + encodeURIComponent(opt.doc.id) + '/archive-to'
      : '/api/kb/' + encodeURIComponent(opt.ds.id) + '/archive-to';

    var w = busyModal('正在归档',
      '<p>正在把内容写入 <strong>' + esc(targetName(target)) + '</strong> 并重建索引…</p>' +
      '<p class="kbm-note"><span class="sp"></span>请稍候，不要关闭页面。</p>');

    try {
      var d = await api(url, { method: 'POST', body: JSON.stringify({ target: target, deleteSource: delSrc }) });
      w.close();
      showArchResult(d);
      if (st.view === 'ds') await loadDs();
      else if (st.view === 'docs') await loadDocs();
    } catch (e) {
      w.close();
      toast(e.message, true);
    }
  }

  function targetName(id) {
    var x = (st.retrieval.items || []).filter(function (y) { return y.id === id; })[0];
    return x ? x.name : id.slice(0, 8);
  }

  function showArchResult(d) {
    var arc = d.archived || [];
    var skip = d.skipped || [];
    var lines = arc.map(function (a) {
      return '<li>' + esc(a.name) + ' —— ' + a.segments + ' 段 / ' + a.chars + ' 字' +
        (a.skippedSegments ? '<em class="dim">（跳过 ' + a.skippedSegments + ' 条未启用分段）</em>' : '') +
        ' · ' + (a.indexingStatus === 'completed' ? '索引已完成' : ('索引 ' + esc(a.indexingStatus))) + '</li>';
    }).join('');
    var skLines = skip.map(function (s) {
      return '<li>' + esc(s.name) + ' —— <em class="dgr-txt">' + esc(s.reason) + '</em></li>';
    }).join('');

    var body =
      '<p>已归档到检索库 <strong>' + esc((d.target || {}).name || '') + '</strong>' +
      (d.deletedSource ? '，并已从源库删除' : '') + '。</p>' +
      (lines ? '<ul class="kbm-list">' + lines + '</ul>' : '') +
      (skLines ? '<p class="dgr-txt">未归档：</p><ul class="kbm-list">' + skLines + '</ul>' : '') +
      '<p class="kbm-note">' + ICON.info + ' 现在去<strong>新建对话</strong>提问相关内容，就能检索到了。</p>';

    modal({ title: '归档完成', body: body, confirmText: '好' });
  }


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
      errorBox(e.message);
    }
  }

  function renderDs() {
    if (!st.ds.items.length) {
      emptyBox(ICON.kb, st.ds.keyword ? '没有找到知识库' : '还没有知识库',
        st.ds.keyword
          ? '没有匹配「' + esc(st.ds.keyword) + '」的知识库，换个词试试，或点「清空」。'
          : '在问答页上传一份知识文件，就会自动建库；也可以点右上角刷新。');
      setCount('');
      return;
    }
    setCount('共 ' + st.ds.total + ' 个' +
      (st.retrieval.ids.length ? ' · 检索库 ' + st.retrieval.ids.length + ' 个' : ''));
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
      var isRetr = isRetrieval(d.id);

      return '<div class="kb-card' + (isRetr ? ' retr' : '') + '" data-ds="' + esc(d.id) + '">' +
        '<div class="kbc-top">' +
          '<span class="kbc-ic">' + ICON.kb + '</span>' +
          '<div class="kbc-title">' +
            '<div class="kbc-name">' + esc(d.name) +
              (isRetr ? '<span class="kb-state retr">检索库</span>' : '') +
              stateBadge(d) + '</div>' +
            '<div class="kbc-desc">' + (d.description ? esc(d.description) : '<em class="dim">无描述</em>') + '</div>' +
          '</div>' +
          '<div class="kbc-nums">' +
            '<div class="kbc-stat"><b>' + (d.documentCount || 0) + '</b><span>文档</span></div>' +
            '<div class="kbc-stat"><b>' + fmtNum(d.wordCount || 0) + '</b><span>字数</span></div>' +
          '</div>' +
        '</div>' +
        '<div class="kbc-meta">' +
          '<span class="kbc-m">' + meta + '</span>' +
          (counts.length ? '<span class="kbc-cnt">' + counts.join('') + '</span>' : '') +
          '<span class="kbc-time">更新于 ' + fmtTime(d.updatedAt) + '</span>' +
        '</div>' +
        '<div class="kbc-acts">' +
          '<button class="btn pri sm" data-act="docs" data-id="' + esc(d.id) + '">' + ICON.doc + '文档管理</button>' +
          (isRetr ? '' :
            '<button class="btn gho sm" data-act="arch" data-id="' + esc(d.id) + '">' + ICON.retr + '归档到检索库</button>') +
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
      errorBox(e.message);
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
      emptyBox(ICON.doc, '这里还没有文档', '在问答页上传知识文件会自动入库，也可以点右上角刷新。');
      setCount('共 ' + st.docs.total + ' 个');
      return;
    }
    setCount('共 ' + st.docs.total + ' 个');
    $('kbBody').innerHTML = st.docs.items.map(function (d) {
      return '<div class="kb-row" data-doc="' + esc(d.id) + '">' +
        '<span class="kbr-ic">' + ICON.doc + '</span>' +
        '<div class="kbr-main">' +
          '<div class="kbr-name">' +
            (d.position != null ? '<span class="kbr-no">#' + d.position + '</span>' : '') +
            esc(d.name) + docStatusText(d) + '</div>' +
          '<div class="kbr-sub">' + fmtNum(d.wordCount || 0) + ' 字 · ' + (d.tokens || 0) + ' tokens' +
            (d.hitCount ? ' · 命中 ' + d.hitCount + ' 次' : '') +
            ' · ' + fmtTime(d.createdAt) +
            (d.error ? ' · <em class="dgr-txt">' + esc(String(d.error).slice(0, 60)) + '</em>' : '') +
          '</div>' +
        '</div>' +
        '<div class="kbr-acts">' +
          '<button class="btn pri sm" data-act="segs" data-id="' + esc(d.id) + '">' + ICON.seg + '分段</button>' +
          (st.cur && isRetrieval(st.cur.id) ? '' :
            '<button class="btn gho sm" data-act="archdoc" data-id="' + esc(d.id) + '">' + ICON.retr + '归档到检索库</button>') +
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
      errorBox(e.message);
    }
  }

  var SEG_CLAMP_CHARS = 260;   // 超过这个长度先折叠，避免一屏只有一条分段

  function renderSegs() {
    var why = segBlockedReason();
    var warn = why ? '<div class="kb-warn">' + ICON.info + '<span>' + esc(why) + '</span></div>' : '';
    setCount('共 ' + st.segs.total + ' 段');
    if (!st.segs.items.length) {
      var hint = st.segs.keyword
        ? '没有匹配「' + esc(st.segs.keyword) + '」的分段，换个词试试，或点「清空」。'
        : '这个文档还没有分段 —— 点上面的「＋ 新增分段」就能手工加一条。';
      $('kbBody').innerHTML = warn +
        '<div class="kb-empty">' +
          '<div class="kbe-ic">' + ICON.seg + '</div>' +
          '<b>' + (st.segs.keyword ? '没有找到分段' : '还没有分段') + '</b>' +
          '<p>' + hint + '</p>' +
        '</div>';
      return;
    }
    $('kbBody').innerHTML = warn + st.segs.items.map(function (s) {
      var kw = s.keywords || [];
      var cls = 'kb-seg-item' + (s.enabled ? '' : ' off');
      if (s.position === 1) cls += ' first';
      var long = String(s.content || '').length > SEG_CLAMP_CHARS;
      return '<div class="' + cls + '" data-seg="' + esc(s.id) + '">' +
        '<div class="kbs-head">' +
          '<span class="kbs-no">#' + (s.position != null ? s.position : '-') + '</span>' +
          (s.enabled ? '<span class="kb-state enabled">启用中</span>' : '<span class="kb-state disabled">已禁用</span>') +
          (s.status && s.status !== 'completed' && s.enabled
            ? '<span class="kb-state partial">' + esc(s.status) + '</span>' : '') +
          '<span class="kbs-wc">' + (s.wordCount || 0) + ' 字' +
            (s.tokens ? ' · ' + s.tokens + ' tokens' : '') +
            (s.hitCount ? ' · 命中 ' + s.hitCount + ' 次' : '') + '</span>' +
          '<div class="kbs-acts">' +
            '<button class="btn gho sm" data-act="segedit" data-id="' + esc(s.id) + '">' + ICON.edit + '编辑</button>' +
            '<button class="btn gho sm" data-act="' + (s.enabled ? 'segdisable' : 'segenable') + '" data-id="' + esc(s.id) + '">' +
              ICON.power + (s.enabled ? '禁用' : '启用') + '</button>' +
            '<button class="btn gho sm dgr-txt" data-act="segdel" data-id="' + esc(s.id) + '">' + ICON.trash + '删除</button>' +
          '</div>' +
        '</div>' +
        (kw.length ? '<div class="kbs-kw">' + kw.map(function (k) {
          return '<span class="kbs-chip">' + esc(k) + '</span>';
        }).join('') + '</div>' : '') +
        '<pre class="kbs-content' + (long ? ' clamp' : '') + '">' + esc(s.content) + '</pre>' +
        (long ? '<button class="kbs-more" data-act="segtoggle" data-id="' + esc(s.id) + '">展开全文</button>' : '') +
        (s.error ? '<div class="kbs-err">' + esc(String(s.error).slice(0, 120)) + '</div>' : '') +
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
      '<label class="kbm-chk"><input type="checkbox" id="kbeRetr"' + (isRetrieval(d.id) ? ' checked' : '') +
        '> 列入「检索库」<em class="dim">（新建对话能检索到这个库；需与 Dify 工作流的检索范围一致）</em></label>' +
      '<p class="kbm-note">' + ICON.info + ' 检索方式为「' + esc(rt.searchMethod || 'semantic_search') +
        '」、向量模型 ' + esc(d.embeddingModel || '—') + ' 保持不变。改动只影响后续检索。</p>';

    return modal({ title: '编辑知识库', body: body, confirmText: '保存' }).then(function (ok) {
      if (!ok) return null;
      var name = $('kbeName').value.trim();
      if (!name) { toast('名称不能为空', true); return null; }
      return {
        name: name,
        description: $('kbeDesc').value.trim(),
        retrievalList: $('kbeRetr').checked,
        wasRetrievalList: isRetrieval(d.id),
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
    // retrievalList 是前端侧的登记项（哪些库在工作流的检索范围里），不走 Dify 的 PATCH
    var listFlag = patch.retrievalList;
    var listChanged = (listFlag !== undefined) && (listFlag !== patch.wasRetrievalList);
    delete patch.retrievalList;
    delete patch.wasRetrievalList;
    busy(btn, true, '保存中');
    try {
      await api('/api/kb/' + encodeURIComponent(d.id), { method: 'PATCH', body: JSON.stringify(patch) });
      if (listChanged) {
        await api('/api/kb/retrieval', {
          method: 'POST',
          body: JSON.stringify(listFlag ? { add: d.id } : { remove: d.id })
        });
        await loadRetrieval();
      }
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
      if (act === 'arch' && ds) { archDialog({ mode: 'ds', ds: ds }); return; }
      if (act === 'del') { delDs(ds, btn); return; }

      // 文档级
      var doc = st.docs.items.filter(function (x) { return x.id === id; })[0];
      if (act === 'segs' && doc) { openSegs(doc); return; }
      if (act === 'archdoc' && doc) { archDialog({ mode: 'doc', ds: st.cur, doc: doc }); return; }
      if (act === 'doenable' || act === 'dodisable') { setDocEnabled(doc, act === 'doenable', btn); return; }
      if (act === 'archive' || act === 'unarchive') { setDocArchived(doc, act === 'archive', btn); return; }
      if (act === 'docdel') { delDoc(doc, btn); return; }

      // 分段级
      if (act === 'segtoggle') {
        var p = btn.closest('.kb-seg-item');
        var pre = p ? p.querySelector('.kbs-content') : null;
        if (pre) {
          var on = pre.classList.toggle('clamp');
          btn.textContent = on ? '展开全文' : '收起';
        }
        return;
      }
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
    // 按钮里带 svg 时 e.target 可能是 svg/path，用 closest 找按钮本身
    if (t.closest && t.closest('#kbSegAdd')) { addSegDialog(); return; }
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

  /* ---------------- 分段：新增 ---------------- */
  /* Dify 只允许在「索引完成 + 启用中」的文档里加分段，先在这里拦一道，给出能看懂的原因 */
  function segBlockedReason() {
    if (!st.curDoc) return '请先进入一个文档';
    if (st.curDoc.archived) return '这个文档已归档 —— 请先返回文档列表点「取消归档」，再新增分段';
    if (!st.curDoc.enabled) return '这个文档已被禁用 —— 请先返回文档列表把它「启用」，再新增分段';
    var ix = st.curDoc.indexingStatus;
    if (ix && ix !== 'completed') return '这个文档还在建立索引（' + ix + '）—— 等它变成「可用」后再新增分段';
    return '';
  }

  /* 想一次加多条时，用单独一行 --- 分隔，比反复开弹窗顺手 */
  function splitBlocks(text) {
    return String(text || '').split(/\n[ \t]*-{3,}[ \t]*\n/)
      .map(function (s) { return s.trim(); })
      .filter(function (s) { return s.length > 0; });
  }

  function addSegDialog() {
    var why = segBlockedReason();
    if (why) { toast(why, true); return; }

    var qa = !!(st.curDoc && st.curDoc.docForm === 'qa_model');
    var body =
      (qa
        ? '<label class="kbm-fg"><span>问题</span>' +
            '<textarea id="ksnContent" rows="3" class="mono" placeholder="例如：院前急救的基本原则是什么？"></textarea></label>' +
          '<label class="kbm-fg"><span>答案</span>' +
            '<textarea id="ksnAnswer" rows="4" class="mono" placeholder="这条问题对应的答案"></textarea></label>'
        : '<label class="kbm-fg"><span>分段内容</span>' +
            '<textarea id="ksnContent" rows="9" class="mono" placeholder="直接粘贴一段内容即可。想一次加多条，就用单独一行 --- 把它们隔开。"></textarea>' +
            '<em class="kbm-tip">每一段都会成为一条独立分段，检索时单独命中。</em></label>') +
      '<label class="kbm-fg"><span>关键词（逗号分隔，可留空）</span>' +
        '<input type="text" id="ksnKw" placeholder="例如：高原病, 吸氧">' +
        '<em class="kbm-tip">给关键词检索用；留空只走语义检索，一般也能命中。</em></label>' +
      '<p class="kbm-note">' + ICON.info +
        ' 新增后 Dify 会立刻为它建立索引（通常几秒）。这个动作<strong>只往知识库加内容</strong>，不影响工作流。</p>';

    modal({ title: qa ? '新增问答对' : '新增分段', body: body, confirmText: '创建并索引' })
      .then(function (ok) {
        if (!ok) return;
        var content = ($('ksnContent') || {}).value || '';
        if (!content.trim()) { toast('分段内容不能为空', true); return; }
        var answer = qa ? (($('ksnAnswer') || {}).value || '') : '';
        var keywords = (($('ksnKw') || {}).value || '').split(/[,，\s]+/).filter(function (s) { return s; });

        var payload;
        if (qa) {
          payload = { content: content.trim(), keywords: keywords };
          if (answer.trim()) payload.answer = answer.trim();
        } else {
          var blocks = splitBlocks(content);
          payload = (blocks.length > 1)
            ? { segments: blocks.map(function (c) { return { content: c, keywords: keywords }; }) }
            : { content: content.trim(), keywords: keywords };
        }
        createSeg(payload);
      });
  }

  async function createSeg(payload) {
    var n = payload.segments ? payload.segments.length : 1;
    try {
      var d = await api('/api/kb/' + encodeURIComponent(st.cur.id) + '/documents/' + encodeURIComponent(st.curDoc.id) +
        '/segments', { method: 'POST', body: JSON.stringify(payload) });
      toast('已新增 ' + (d.created || n) + ' 条分段，正在建立索引');
      await loadSegs();
    } catch (e) { toast(e.message, true); }
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
  window.__kb = {
    modal: modal, confirmBox: confirmBox, esc: esc, api: api, state: st, render: render,
    loadRetrieval: loadRetrieval, archDialog: archDialog, isRetrieval: isRetrieval,
    setRetrieval: setRetrieval, busyModal: busyModal,
    addSegDialog: addSegDialog, createSeg: createSeg, segBlockedReason: segBlockedReason,
    splitBlocks: splitBlocks, renderSegs: renderSegs
  };
})();
