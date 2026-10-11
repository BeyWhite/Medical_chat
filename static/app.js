/* =========================================================================
   医学知识智能问答 · 前端（问答5.0 版）
   后端：本机 Python 服务（账号 + 多会话 + Dify 代理 + 文件上传）
   接口：POST /api/upload → 转发 Dify /files/upload，返回 {ok, file:{id,name,size}}
        POST /api/chat   →  SSE: title / delta / replace / final / meta / end / error
   ========================================================================= */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var LS_THEME = 'mq50.theme';
  var LS_TOKEN = 'mq50_token';

  var state = {
    token: localStorage.getItem(LS_TOKEN) || '',
    user: null,
    conversations: [],
    currentId: null,
    messages: [],
    attachments: [],
    busy: false,
    uploading: 0,
    controller: null,
    settings: null,
    authMode: 'login'
  };

  /* ==================== 工具 ==================== */
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function toast(msg, isErr) {
    var t = $('toast');
    t.textContent = msg;
    t.className = 'toast on' + (isErr ? ' err' : '');
    clearTimeout(t._t);
    t._t = setTimeout(function () { t.className = 'toast'; }, 2400);
  }
  function timeStr(ts) {
    var d = ts ? new Date(ts * 1000) : new Date();
    return ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
  }
  function sec(v) { return (v / 1000).toFixed(1) + 's'; }

  function copyText(t) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(t);
    return new Promise(function (res) {
      var ta = document.createElement('textarea');
      ta.value = t; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); } catch (e) {}
      document.body.removeChild(ta); res();
    });
  }

  /* ==================== Markdown ==================== */
  function inline(t) {
    t = esc(t);
    t = t.replace(/`([^`\n]+)`/g, '<code>$1</code>');
    t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    t = t.replace(/__([^_]+)__/g, '<strong>$1</strong>');
    t = t.replace(/(^|[^*\w])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    t = t.replace(/~~([^~]+)~~/g, '<del>$1</del>');
    t = t.replace(/\[([^\]\n]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    return t;
  }
  function renderMD(src) {
    if (!src) return '';
    var blocks = [];
    src = String(src).replace(/```[ \t]*[\w+-]*\n?([\s\S]*?)```/g, function (m, code) {
      blocks.push(code.replace(/\n$/, ''));
      return '\u0001C' + (blocks.length - 1) + '\u0001';
    });
    var lines = src.split('\n'), out = [], para = [], list = null;
    function flushPara() {
      if (para.length) { out.push('<p>' + inline(para.join('\n')).replace(/\n/g, '<br>') + '</p>'); para = []; }
    }
    function closeList() { if (list) { out.push(list === 'ul' ? '</ul>' : '</ol>'); list = null; } }
    var i = 0;
    while (i < lines.length) {
      var t = lines[i].replace(/\s+$/, '').trim();
      if (!t) { flushPara(); closeList(); i++; continue; }
      var m;
      if ((m = t.match(/^\u0001C(\d+)\u0001$/))) {
        flushPara(); closeList();
        out.push('<pre><code>' + esc(blocks[+m[1]]) + '</code></pre>'); i++; continue;
      }
      if (/^\|.*\|$/.test(t) && i + 1 < lines.length && /^\|[\s:|-]+\|$/.test(lines[i + 1].trim())) {
        flushPara(); closeList();
        var head = t.slice(1, -1).split('|').map(function (s) { return s.trim(); });
        i += 2;
        var rows = [];
        while (i < lines.length && /^\|.*\|$/.test(lines[i].trim())) {
          rows.push(lines[i].trim().slice(1, -1).split('|').map(function (s) { return s.trim(); }));
          i++;
        }
        var html = '<table><thead><tr>';
        head.forEach(function (h) { html += '<th>' + inline(h) + '</th>'; });
        html += '</tr></thead><tbody>';
        rows.forEach(function (r) {
          html += '<tr>';
          for (var k = 0; k < head.length; k++) html += '<td>' + inline(r[k] || '') + '</td>';
          html += '</tr>';
        });
        out.push(html + '</tbody></table>');
        continue;
      }
      if ((m = t.match(/^(#{1,6})\s+(.*)$/))) {
        flushPara(); closeList();
        var lv = Math.min(m[1].length + 1, 5);
        out.push('<h' + lv + '>' + inline(m[2]) + '</h' + lv + '>'); i++; continue;
      }
      if (/^([-*_])\1{2,}$/.test(t)) { flushPara(); closeList(); out.push('<hr>'); i++; continue; }
      if ((m = t.match(/^>\s?(.*)$/))) { flushPara(); closeList(); out.push('<blockquote>' + inline(m[1]) + '</blockquote>'); i++; continue; }
      if ((m = t.match(/^[-*+]\s+(.*)$/))) {
        flushPara();
        if (list !== 'ul') { closeList(); out.push('<ul>'); list = 'ul'; }
        out.push('<li>' + inline(m[1]) + '</li>'); i++; continue;
      }
      if ((m = t.match(/^\d+[.)]\s+(.*)$/))) {
        flushPara();
        if (list !== 'ol') { closeList(); out.push('<ol>'); list = 'ol'; }
        out.push('<li>' + inline(m[1]) + '</li>'); i++; continue;
      }
      para.push(t); i++;
    }
    flushPara(); closeList();
    return out.join('');
  }

  /* ==================== 图标 ==================== */
  var ICON = {
    ai: '<svg viewBox="0 0 24 24"><path d="M12 3v3M12 18v3M5 12H2M22 12h-3M6.3 6.3 4.2 4.2M19.8 19.8l-2.1-2.1M17.7 6.3l2.1-2.1M4.2 19.8l2.1-2.1"/><circle cx="12" cy="12" r="4.2"/></svg>',
    user: '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="3.6"/><path d="M4.8 20.2a7.4 7.4 0 0 1 14.4 0"/></svg>',
    copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/></svg>',
    done: '<svg viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg>',
    clock: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.2 2"/></svg>',
    token: '<svg viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h10"/></svg>',
    src: '<svg viewBox="0 0 24 24"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H19a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H6.5A2.5 2.5 0 0 0 4 21.5"/><path d="M4 5.5v16M8 8h8M8 12h5"/></svg>',
    warn: '<svg viewBox="0 0 24 24"><path d="M12 3 2.5 20h19L12 3Z"/><path d="M12 10v4M12 17.2v.1"/></svg>',
    dot: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/></svg>',
    chat: '<svg viewBox="0 0 24 24"><path d="M21 12a8 8 0 0 1-8 8H8l-4 3v-4.6A8 8 0 0 1 5 10a8 8 0 0 1 8-8h.5A8 8 0 0 1 21 12Z"/></svg>',
    heart: '<svg viewBox="0 0 24 24"><path d="M20.4 5.6a5 5 0 0 0-7.1 0L12 6.9l-1.3-1.3a5 5 0 0 0-7.1 7.1L12 21l8.4-8.3a5 5 0 0 0 0-7.1Z"/></svg>',
    activity: '<svg viewBox="0 0 24 24"><path d="M3 12h4l2.4-6 3.8 12 2.4-6h5.4"/></svg>',
    list: '<svg viewBox="0 0 24 24"><path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.4" cy="6" r="1.4"/><circle cx="4.4" cy="12" r="1.4"/><circle cx="4.4" cy="18" r="1.4"/></svg>',
    flag: '<svg viewBox="0 0 24 24"><path d="M5.5 21V3.5M5.5 4.5h12.5l-2.2 4.2 2.2 4.2H5.5"/></svg>',
    sun: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2.6M12 19.4V22M2 12h2.6M19.4 12H22M5.2 5.2l1.9 1.9M16.9 16.9l1.9 1.9M18.8 5.2l-1.9 1.9M7.1 16.9l-1.9 1.9"/></svg>',
    wind: '<svg viewBox="0 0 24 24"><path d="M3 8h11a3 3 0 1 0-3-3M3 12h15a3 3 0 1 1-3 3M3 16.5h8"/></svg>',
    file: '<svg viewBox="0 0 24 24"><path d="M13 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V8l-5-5Z"/><path d="M13 3v5h5"/></svg>',
    clip: '<svg viewBox="0 0 24 24"><path d="M21.4 11.05 12.3 20.2a5.5 5.5 0 0 1-7.8-7.8l8.5-8.5a3.7 3.7 0 0 1 5.2 5.2l-8.5 8.5a1.85 1.85 0 0 1-2.6-2.6l7.8-7.8"/></svg>',
    x: '<svg viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg>',
    upload: '<svg viewBox="0 0 24 24"><path d="M12 16V4m0 0L8 8m4-4 4 4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>',
    kb: '<svg viewBox="0 0 24 24"><path d="M4 6.5A2.5 2.5 0 0 1 6.5 4H20v16H6.5A2.5 2.5 0 0 0 4 22.5"/><path d="M4 6.5v16"/></svg>'
  };

  /* ==================== 欢迎页 ==================== */
  var SAMPLES = [
    { ic: 'activity', t: '什么是院前急救', d: '院前急救的定义与救护阶段' },
    { ic: 'list', t: '院前急救的原则有哪些', d: '先排险后施救等七条基本原则' },
    { ic: 'flag', t: '现场急救的分区及标记有哪些', d: '红黄绿黑四区的优先级划分' },
    { ic: 'heart', t: '心脏骤停的临床表现有哪些', d: '意识、循环、呼吸、瞳孔的变化时序' },
    { ic: 'sun', t: '中暑的救治措施有哪些', d: '脱离高温、迅速降温与对症支持' },
    { ic: 'wind', t: '一氧化碳中毒的救治原则是什么', d: '院外急救与院内救护要点' }
  ];

  function renderWelcome() {
    var cards = SAMPLES.map(function (s) {
      return '<button class="card" data-q="' + esc(s.t) + '">' +
        '<div class="card-t">' + ICON[s.ic] + esc(s.t) + '</div>' +
        '<div class="card-d">' + esc(s.d) + '</div></button>';
    }).join('');
    $('thread').innerHTML =
      '<div class="welcome">' +
        '<div class="w-ic"><svg viewBox="0 0 24 24"><path d="M12 2 4 5.5v6c0 5 3.4 9.4 8 10.5 4.6-1.1 8-5.5 8-10.5v-6L12 2Zm-1 13.5-3-3 1.4-1.4 1.6 1.6 4.2-4.2 1.4 1.4-5.6 5.6Z"/></svg></div>' +
        '<h2>医学知识智能问答 · 问答5.0</h2>' +
        '<p>基于 Dify 应用「问答5.0」：提问时先由模型生成 5 个相似问，逐问检索知识库后去重汇总作答；' +
        '点左下角「📎 上传知识文件」可把资料自动生成问答对并写入知识库。以下问题均已实测可精准召回：</p>' +
        '<div class="cards">' + cards + '</div>' +
        '<div class="notice">' + ICON.warn +
          '<div><b>耗时提示：</b>问答分支要跑「5 个相似问 → 逐问检索作答 → 汇总」，实测整体约 <b>3~4 分钟</b>；' +
          '文件入库分支约 <b>1~2 分钟</b>。回答过程中可随时点「停止」。</div>' +
        '</div>' +
        '<div class="notice">' + ICON.warn +
          '<div><b>免责声明：</b>本页面由 AI 依据知识库检索生成，仅供医学知识学习与参考，' +
          '<b>不能替代执业医师的现场判断与专业救治</b>。遇紧急情况请立即拨打 120。</div>' +
        '</div>' +
      '</div>';
    Array.prototype.forEach.call($('thread').querySelectorAll('.card'), function (el) {
      el.addEventListener('click', function () {
        $('q').value = el.getAttribute('data-q');
        autoGrow(); $('q').focus(); send();
      });
    });
  }

  /* ==================== 消息节点 ==================== */
  function msgNode(role, content, extra) {
    extra = extra || {};
    var wrap = document.createElement('div');
    wrap.className = 'msg ' + (role === 'user' ? 'user' : 'ai');
    var who = role === 'user' ? '我' : '医学助手';
    var cls = 'bub' + (extra.error ? ' err' : '');
    wrap.innerHTML =
      '<div class="av ' + (role === 'user' ? 'user' : 'ai') + '">' + (role === 'user' ? ICON.user : ICON.ai) + '</div>' +
      '<div class="body">' +
        '<div class="who">' + who + ' · ' + (extra.time || timeStr(extra.createdAt)) + '</div>' +
        '<div class="' + cls + '"></div>' +
        '<div class="src-slot"></div>' +
        '<div class="meta-slot"></div>' +
        '<div class="acts"></div>' +
      '</div>';
    var bub = wrap.querySelector('.bub');
    if (role === 'user') {
      var filesHtml = (extra.files && extra.files.length)
        ? '<div class="msg-files">' + extra.files.map(function (f) {
            return '<span class="msg-file">' + ICON.file + esc(f.name || '知识文件') + '</span>';
          }).join('') + '</div>'
        : '';
      bub.textContent = content;
      if (filesHtml) bub.insertAdjacentHTML('afterbegin', filesHtml);
    }
    else if (extra.error) bub.innerHTML = '<span class="err-t">' + esc(extra.errorTitle || '请求失败') + '</span>' + esc(content);
    else bub.innerHTML = renderMD(content);
    return wrap;
  }
  function addMsg(role, content, extra) {
    var thread = $('thread');
    var w = thread.querySelector('.welcome');
    if (w) thread.innerHTML = '';
    var node = msgNode(role, content, extra);
    thread.appendChild(node);
    return node;
  }
  function scrollBottom(force) {
    var s = $('scroll');
    var near = s.scrollHeight - s.scrollTop - s.clientHeight < 170;
    if (force || near) s.scrollTop = s.scrollHeight;
  }
  function addActions(node, text) {
    var acts = node.querySelector('.acts');
    acts.innerHTML = '';
    var b = document.createElement('button');
    b.className = 'act';
    b.innerHTML = ICON.copy + '复制';
    b.addEventListener('click', function () {
      copyText(text).then(function () {
        b.innerHTML = ICON.done + '已复制';
        setTimeout(function () { b.innerHTML = ICON.copy + '复制'; }, 1500);
      });
    });
    acts.appendChild(b);
    acts.classList.add('show');
  }
  function addMeta(node, info) {
    var slot = node.querySelector('.meta-slot');
    if (!slot) return;
    info = info || {};
    var chips = [];
    if (info.latencyMs) chips.push('<span class="chip">' + ICON.clock + sec(info.latencyMs) + '</span>');
    if (info.firstTokenMs) chips.push('<span class="chip">' + ICON.dot + '首字 ' + sec(info.firstTokenMs) + '</span>');
    if (info.tokens) chips.push('<span class="chip">' + ICON.token + info.tokens + ' tokens</span>');
    if (info.model) chips.push('<span class="chip">' + esc(info.model) + '</span>');
    if (info.ingest) {
      chips.push('<span class="chip hit">' + ICON.kb + '知识文件入库' +
        (info.ingestFiles && info.ingestFiles.length ? '（' + esc(info.ingestFiles[0]) + '）' : '') + '</span>');
    } else if (typeof info.kb === 'number') {
      chips.push(info.kb > 0
        ? '<span class="chip hit">' + ICON.src + '知识库命中 ' + info.kb + ' 条</span>'
        : '<span class="chip miss">' + ICON.warn + '未命中知识库（由模型自有知识作答）</span>');
    }
    slot.innerHTML = chips.length ? '<div class="meta">' + chips.join('') + '</div>' : '';
  }
  function addSources(node, list) {
    var slot = node.querySelector('.src-slot');
    if (!slot) return;
    if (!list || !list.length) { slot.innerHTML = ''; return; }
    var rows = list.map(function (r) {
      var ds = r.dataset ? ('数据集：' + r.dataset) : '';
      var sc = (typeof r.score === 'number') ? (' · 相关度 ' + r.score.toFixed(3)) : '';
      var pos = r.position ? (' · 第 ' + r.position + ' 条') : '';
      var wc = r.wordCount ? (' · ' + r.wordCount + ' 字') : '';
      return '<div class="doc"><b>' + esc(r.name || '知识片段') + '</b>' +
        '<span class="sc">' + esc(ds + sc + pos + wc) + '</span>' +
        '<p>' + esc(r.content || '') + '</p></div>';
    }).join('');
    slot.innerHTML = '<details class="srcs"><summary>' + ICON.src + '知识来源（' + list.length + '）</summary>' + rows + '</details>';
  }

  /* ==================== API ==================== */
  function handleUnauthorized() {
    state.token = ''; state.user = null;
    state.attachments = []; state.uploading = 0;
    localStorage.removeItem(LS_TOKEN);
    $('app').hidden = true;$('authView').hidden = false; $('authView').style.display = 'flex';
    setAuthMode('login');
    authError('登录状态已失效，请重新登录');
  }

  async function api(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
    if (state.token) opts.headers['Authorization'] = 'Bearer ' + state.token;
    var res = await fetch(path, opts);
    if (res.status === 401) { handleUnauthorized(); throw new Error('未登录或登录已过期'); }
    var data = {};
    try { data = await res.json(); } catch (e) {}
    if (!res.ok || data.ok === false) throw new Error(data.error || ('请求失败 ' + res.status));
    return data;
  }

  /* ==================== 会话列表 ==================== */
  function renderSessions() {
    var box = $('sessList');
    var html = '<div class="side-label">历史对话</div>';
    if (!state.conversations.length) {
      html += '<div class="side-empty">暂无记录，点击上方「新建对话」开始</div>';
    }
    state.conversations.forEach(function (c) {
      html +=
        '<div class="sess' + (c.id === state.currentId ? ' on' : '') + '" data-id="' + esc(c.id) + '">' +
          '<span class="sess-ic">' + ICON.chat + '</span>' +
          '<span class="sess-name">' + esc(c.title || '新对话') + '</span>' +
          '<span class="sess-del" data-del="' + esc(c.id) + '"><svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13"/></svg></span>' +
        '</div>';
    });
    box.innerHTML = html;
    Array.prototype.forEach.call(box.querySelectorAll('.sess'), function (el) {
      el.addEventListener('click', function (e) {
        if (e.target.closest('[data-del]')) return;
        openConversation(el.getAttribute('data-id'));
        if (window.innerWidth <= 860) $('side').classList.remove('on');
      });
    });
    Array.prototype.forEach.call(box.querySelectorAll('[data-del]'), function (el) {
      el.addEventListener('click', function (e) {
        e.stopPropagation();
        removeConversation(el.getAttribute('data-del'));
      });
    });
  }

  async function loadConversations() {
    try {
      var d = await api('/api/conversations');
      state.conversations = d.conversations || [];
      renderSessions();
    } catch (e) { toast(e.message, true); }
  }

  async function newConversation(silent) {
    try {
      var d = await api('/api/conversations', { method: 'POST', body: JSON.stringify({}) });
      state.conversations.unshift({ id: d.conversation.id, title: d.conversation.title, count: 0, updatedAt: Date.now() / 1000 });
      state.currentId = d.conversation.id;
      state.messages = [];
      state.attachments = [];
      renderFilesBar();
      renderSessions();
      $('topTitle').textContent = '新对话';
      renderWelcome();
      if (window.innerWidth <= 860) $('side').classList.remove('on');
      $('q').focus();
      if (!silent) toast('已新建对话');
    } catch (e) { toast(e.message, true); }
  }

  async function openConversation(id) {
    if (state.busy) { toast('请先停止当前回答'); return; }
    if (id === state.currentId) return;
    state.currentId = id;
    state.attachments = [];
    renderFilesBar();
    renderSessions();
    try {
      var d = await api('/api/conversations/' + encodeURIComponent(id) + '/messages');
      state.messages = (d.messages || []).map(function (m) { return Object.assign({}, m, { createdAt: m.createdAt }); });
      $('topTitle').textContent = d.title || '新对话';
      renderThread();
    } catch (e) { toast(e.message, true); }
  }

  async function removeConversation(id) {
    if (state.busy) { toast('请先停止当前回答'); return; }
    if (!confirm('删除这条对话记录？此操作不可恢复。')) return;
    try {
      await api('/api/conversations/' + encodeURIComponent(id), { method: 'DELETE' });
      state.conversations = state.conversations.filter(function (c) { return c.id !== id; });
      if (state.currentId === id) {
        if (state.conversations.length) {
          state.currentId = null;
          await openConversation(state.conversations[0].id);
        } else {
          state.currentId = null;
          state.messages = [];
          $('topTitle').textContent = '新对话';
          renderWelcome();
        }
      }
      renderSessions();
      toast('已删除');
    } catch (e) { toast(e.message, true); }
  }

  function renderThread() {
    var thread = $('thread');
    thread.innerHTML = '';
    if (!state.messages.length) { renderWelcome(); return; }
    state.messages.forEach(function (m) {
      var node = addMsg(m.role, m.content, {
        error: m.error, errorTitle: m.errorTitle, createdAt: m.createdAt,
        files: (m.meta && m.meta.files) || null
      });
      if (m.role !== 'user') {
        if (m.meta) addMeta(node, m.meta);
        if (m.sources) addSources(node, m.sources);
        if (!m.error) addActions(node, m.content);
      }
    });
    $('scroll').scrollTop = $('scroll').scrollHeight;
  }

  /* ==================== 知识文件上传 ==================== */
  function fmtSize(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1024 / 1024).toFixed(2) + ' MB';
  }
  function renderFilesBar() {
    var bar = $('filesBar');
    if (!bar) return;
    if (!state.attachments.length && !state.uploading) { bar.hidden = true; bar.innerHTML = ''; return; }
    bar.hidden = false;
    var html = state.attachments.map(function (f, i) {
      return '<span class="fchip">' + ICON.file + '<b>' + esc(f.name) + '</b>' +
        '<em>' + fmtSize(f.size) + '</em>' +
        '<span class="frm" data-rm="' + i + '" title="移除">' + ICON.x + '</span></span>';
    }).join('');
    if (state.uploading) {
      html += '<span class="fchip up"><span class="spin"></span><b>上传中…</b></span>';
    }
    html += '<span class="fhint">' + ICON.kb + '将自动生成问答对并写入知识库（一次 1 个文件）</span>';
    bar.innerHTML = html;
  }
  function setAttachBusy(v) {
    var b = $('btnAttach');
    if (!b) return;
    b.classList.toggle('busy', !!v);
    b.disabled = !!v;
  }
  async function uploadFiles(fileList) {
    var files = Array.prototype.slice.call(fileList || []);
    if (!files.length) return;
    if (!state.token) { handleUnauthorized(); return; }
    var f = files[0];
    if (files.length > 1) toast('一次只能上传 1 个知识文件，已取第一个：' + f.name, true);
    if (f.size > 15 * 1024 * 1024) {
      toast(f.name + ' 超过 Dify 的 15MB 上限，请压缩后再传', true);
      return;
    }
    if (state.attachments.length) {
      state.attachments = [];
      toast('一次只能挂 1 个文件，已替换为：' + f.name, true);
    }
    state.uploading = 1;
    setAttachBusy(true);
    renderFilesBar();
    try {
      var fd = new FormData();
      fd.append('file', f);
      var res = await fetch('/api/upload', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + state.token },
        body: fd
      });
      var d = await res.json().catch(function () { return {}; });
      if (res.status === 401) { handleUnauthorized(); throw new Error('登录状态已失效'); }
      if (!res.ok || !d.ok) throw new Error(d.error || ('上传失败 ' + res.status));
      state.attachments.push({ id: d.file.id, name: d.file.name || f.name, size: d.file.size || f.size });
      toast('已上传：' + (d.file.name || f.name) + '，发送后自动入库');
    } catch (e) {
      toast(e.message, true);
    } finally {
      state.uploading = 0;
      setAttachBusy(false);
      renderFilesBar();
    }
  }

  /* ==================== 输入框 ==================== */
  function autoGrow() {
    var ta = $('q');
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 190) + 'px';
  }
  function setBusy(v) {
    state.busy = v;
    var btn = $('send'), box = $('box');
    if (v) {
      btn.classList.add('stop');
      $('sendTxt').textContent = '停止';
      $('sendIcon').outerHTML = '<span class="sp" id="sendIcon"></span>';
      box.classList.add('busy');
    } else {
      btn.classList.remove('stop');
      $('sendTxt').textContent = '发送';
      var sp = $('sendIcon');
      if (sp) sp.outerHTML = '<svg viewBox="0 0 24 24" id="sendIcon"><path d="M4 12l16-8-6 8 6 8-16-8Z"/></svg>';
      box.classList.remove('busy');
    }
  }

  /* ==================== 发送 / 流式接收 ==================== */
  async function send() {
    if (state.busy) return;
    if (state.uploading) { toast('文件还在上传中，请稍候'); return; }
    var ta = $('q');
    var text = ta.value.trim();
    var files = state.attachments.slice();
    var ingest = files.length > 0;
    if (!text && !ingest) { toast('请输入问题，或先上传知识文件'); return; }
    if (!text) text = '请处理我上传的医学知识文件';
    if (!state.currentId) { await newConversation(true); }

    state.messages.push({
      role: 'user', content: text,
      meta: ingest ? { files: files } : null,
      createdAt: Date.now() / 1000
    });
    ta.value = ''; autoGrow();
    addMsg('user', text, { files: ingest ? files : null });
    state.attachments = []; renderFilesBar();
    scrollBottom(true);

    var node = addMsg('assistant', '');
    var bub = node.querySelector('.bub');
    bub.innerHTML = '<div class="think"><span class="spin"></span><span>' +
      (ingest
        ? '正在提取文件正文并生成问答对，写入知识库…（约 1~2 分钟）'
        : '正在生成 5 个相似问、逐条检索知识库，再汇总作答…（约 3~4 分钟）') +
      '</span></div>';
    setBusy(true);

    var t0 = performance.now();
    var answer = '', firstTokenAt = 0, meta = {}, sources = [], gotError = false, raf = 0;

    function paint() {
      if (raf) return;
      raf = requestAnimationFrame(function () {
        raf = 0;
        bub.innerHTML = (renderMD(answer) || '') + (state.busy ? '<span class="caret"></span>' : '');
        scrollBottom();
      });
    }

    state.controller = new AbortController();
    var watchdog = setTimeout(function () { if (state.controller) state.controller.abort(); }, 900000);

    try {
      var res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + state.token },
        body: JSON.stringify({
          message: text,
          conversationId: state.currentId,
          files: files.map(function (f) { return { id: f.id, name: f.name }; }),
          titleHint: ingest ? ('📎 ' + files[0].name) : ''
        }),
        signal: state.controller.signal
      });

      if (res.status === 401) { handleUnauthorized(); throw new Error('未登录'); }
      if (!res.ok) {
        var raw = await res.text(), msg = raw;
        try { var j = JSON.parse(raw); msg = j.error || j.message || raw; } catch (e) {}
        throw new Error(msg);
      }

      var reader = res.body.getReader(), dec = new TextDecoder('utf-8'), buf = '';

      while (true) {
        var chunk = await reader.read();
        if (chunk.done) break;
        buf += dec.decode(chunk.value, { stream: true });
        var parts = buf.split('\n\n');
        buf = parts.pop();
        for (var p = 0; p < parts.length; p++) {
          var ev = 'message', data = '';
          var lines = parts[p].split('\n');
          for (var l = 0; l < lines.length; l++) {
            if (lines[l].indexOf('event:') === 0) ev = lines[l].slice(6).trim();
            else if (lines[l].indexOf('data:') === 0) data += lines[l].slice(5).trim();
          }
          if (!data) continue;
          var o;
          try { o = JSON.parse(data); } catch (e) { continue; }

          if (ev === 'delta') {
            if (!firstTokenAt) firstTokenAt = performance.now() - t0;
            answer += (o.text || '');
            paint();
          } else if (ev === 'replace') {
            answer = o.text || '';
            paint();
          } else if (ev === 'final') {
            answer = o.text || answer;
            if (!firstTokenAt) firstTokenAt = performance.now() - t0;
            paint();
          } else if (ev === 'meta') {
            meta = o || {};
            if (o && o.sources) sources = o.sources;
          } else if (ev === 'title') {
            $('topTitle').textContent = o.title || '新对话';
            for (var ci = 0; ci < state.conversations.length; ci++) {
              if (state.conversations[ci].id === state.currentId) state.conversations[ci].title = o.title;
            }
            renderSessions();
          } else if (ev === 'error') {
            gotError = true;
            bub.className = 'bub err';
            bub.innerHTML = '<span class="err-t">请求失败</span>' + esc(o.message || '调用失败');
            scrollBottom();
          }
        }
      }

      if (!gotError) {
        if (!answer) {
          bub.className = 'bub err';
          bub.innerHTML = '<span class="err-t">未返回内容</span>请检查 Dify 工作流配置或换个问法重试。';
        } else {
          bub.className = 'bub';
          bub.innerHTML = renderMD(answer);
          var info = {
            latencyMs: meta.latencyMs || Math.round(performance.now() - t0),
            firstTokenMs: meta.firstTokenMs || Math.round(firstTokenAt),
            tokens: meta.tokens || 0,
            model: meta.model || '',
            kb: typeof meta.kb === 'number' ? meta.kb : sources.length,
            ingest: !!meta.ingest,
            ingestFiles: meta.ingestFiles || []
          };
          meta = info;
          addMeta(node, info);
          addSources(node, sources);
          addActions(node, answer);
          state.messages.push({ role: 'assistant', content: answer, meta: info, sources: sources, createdAt: Date.now() / 1000 });
          for (var k = 0; k < state.conversations.length; k++) {
            if (state.conversations[k].id === state.currentId) {
              state.conversations[k].count = (state.conversations[k].count || 0) + 2;
              state.conversations[k].updatedAt = Date.now() / 1000;
            }
          }
          state.conversations.sort(function (a, b) { return (b.updatedAt || 0) - (a.updatedAt || 0); });
          renderSessions();
        }
      }
    } catch (err) {
      var aborted = err && err.name === 'AbortError';
      if (aborted) {
        if (answer) {
          bub.className = 'bub';
          bub.innerHTML = renderMD(answer);
          addActions(node, answer);
          state.messages.push({ role: 'assistant', content: answer, meta: {}, sources: [], createdAt: Date.now() / 1000 });
        } else {
          bub.className = 'bub err';
          bub.innerHTML = '<span class="err-t">已停止</span>本次回答已被手动中止。';
          state.messages.push({ role: 'assistant', content: '本次回答已被手动中止。', error: true, errorTitle: '已停止', createdAt: Date.now() / 1000 });
        }
      } else if (!gotError) {
        var m = (err && err.message) || String(err);
        var title = '请求失败', hint = '';
        if (/429|rate limit|Too Many Requests/i.test(m)) {
          title = '模型限流（429）';
          hint = '该工作流调用的模型为共享额度，短时间内连续提问会被限流。请等待约 10–20 秒后重试。';
        } else if (/Failed to fetch|NetworkError|Load failed/i.test(m)) {
          title = '无法连接服务';
          hint = '请确认本机服务仍在运行，且页面是通过 http://本机IP:8000 打开的。';
        } else if (/无法连接 Dify|Dify 返回错误|非流式响应/.test(m)) {
          title = '无法连接 Dify';
          hint = '请在「接口设置」中检查 API 访问点与密钥，并点击顶栏状态点重新检测。';
        }
        bub.className = 'bub err';
        bub.innerHTML = '<span class="err-t">' + esc(title) + '</span>' +
          (hint ? '<div style="margin-bottom:8px">' + esc(hint) + '</div>' : '') +
          '<details style="font-size:13px;opacity:.85;margin-top:2px"><summary style="cursor:pointer;user-select:none">技术详情</summary>' +
          '<div style="margin-top:7px;font-family:var(--mono);font-size:11.5px;line-height:1.65;word-break:break-all;max-height:160px;overflow:auto">' +
          esc(m.slice(0, 700)) + '</div></details>';
        state.messages.push({ role: 'assistant', content: hint || m, error: true, errorTitle: title, createdAt: Date.now() / 1000 });
      }
    } finally {
      clearTimeout(watchdog);
      state.controller = null;
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
      setBusy(false);
      if (!gotError && answer) bub.innerHTML = renderMD(answer);
      scrollBottom();
    }
  }

  /* ==================== 健康探测 ==================== */
  function setStat(cls, text) {
    $('dot').className = 'dot ' + cls;
    $('statTxt').textContent = text;
  }
  async function healthCheck() {
    setStat('wait', '检测中…');
    $('brandSub').textContent = '问答5.0 · 连接中…';
    try {
      var d = await api('/api/health');
      if (d.ok) {
        setStat('ok', '已连接 · ' + (d.name || '应用'));
        $('brandSub').textContent = (d.name || '问答5.0') + ' · ' + (d.mode || d.appType || '');
        $('badgeMode').textContent = (d.mode === 'advanced-chat' || d.appType === 'chatflow') ? '医学知识库' : (d.appType || '应用');
        return d;
      }
      setStat('bad', d.configured ? '连接失败' : '未配置密钥');
      $('brandSub').textContent = d.configured ? 'Dify 连接失败' : '未配置 Dify';
      return d;
    } catch (e) {
      setStat('bad', '服务异常');
      $('brandSub').textContent = '服务异常';
      return null;
    }
  }

  /* ==================== 主题 ==================== */
  function applyTheme(t) {
    document.documentElement.setAttribute('data-theme', t);
    try { localStorage.setItem(LS_THEME, t); } catch (e) {}
  }

  /* ==================== 设置抽屉 ==================== */
  function buildCandidates() {
    var out = [];
    function push(u) { u = String(u || '').replace(/\/+$/, ''); if (u && out.indexOf(u) < 0) out.push(u); }
    try {
      if (location.protocol === 'http:' || location.protocol === 'https:') {
        push(location.origin + '/v1');
        push(location.origin.replace(/:\d+$/, '') + '/v1');
      }
    } catch (e) {}
    push('http://127.0.0.1/v1');
    push('http://localhost/v1');
    return out;
  }
  var CANDIDATES = buildCandidates();

  function renderQuickBases(cur) {
    var list = CANDIDATES.slice();
    if (list.indexOf(cur) < 0) list.unshift(cur);
    $('quickBases').innerHTML = list.map(function (b) {
      var on = (b.replace(/\/+$/, '') === String(cur).replace(/\/+$/, ''));
      return '<button type="button" class="qbtn" data-base="' + esc(b) + '"' +
        (on ? ' style="border-color:var(--brand);color:var(--brand);font-weight:600"' : '') + '>' + esc(b) + '</button>';
    }).join('');
  }
  function toggleInputVar() {
    $('fgInputVar').hidden = $('cfgType').value !== 'workflow';
  }
  async function openDrawer() {
    try {
      var d = await api('/api/settings');
      state.settings = d;
      $('cfgBase').value = d.baseUrl || '';
      $('cfgKey').value = '';
      $('keyMasked').textContent = d.apiKeyMasked || '未设置';
      $('cfgDsKey').value = '';
      $('dsKeyMasked').textContent = d.datasetIdMasked || '未设置';
      $('cfgType').value = d.appType || 'chatflow';
      $('cfgInputVar').value = d.inputVar || 'query';
      $('cfgInputs').value = JSON.stringify(d.extraInputs || {}, null, 0) === '{}' ? '{}' : JSON.stringify(d.extraInputs, null, 2);
      $('cfgStream').checked = d.stream !== false;
      $('cfgTimeout').value = d.timeout || 300;
      renderQuickBases(d.baseUrl || '');
      toggleInputVar();
      $('mask').classList.add('on'); $('drawer').classList.add('on');
    } catch (e) { toast(e.message, true); }
  }
  function closeDrawer() {
    $('mask').classList.remove('on'); $('drawer').classList.remove('on');
  }
  async function saveSettings() {
    var base = $('cfgBase').value.trim();
    if (!base) { toast('请填写 API 访问点'); return; }
    var iv = $('cfgInputs').value.trim() || '{}';
    try { JSON.parse(iv); } catch (e) { toast('inputs 不是合法 JSON'); return; }
    try {
      var d = await api('/api/settings', {
        method: 'POST',
        body: JSON.stringify({
          baseUrl: base,
          apiKey: $('cfgKey').value.trim(),
          datasetApiKey: $('cfgDsKey').value.trim(),
          appType: $('cfgType').value,
          inputVar: $('cfgInputVar').value.trim() || 'query',
          extraInputs: iv,
          stream: $('cfgStream').checked,
          timeout: parseInt($('cfgTimeout').value, 10) || 300
        })
      });
      closeDrawer();
      toast(d.healthy ? ('已保存 · 连接正常（' + d.name + '）') : '已保存，但连接失败', !d.healthy);
      healthCheck();
    } catch (e) { toast(e.message, true); }
  }

  /* ==================== 导出 ==================== */
  function exportCurrent() {
    if (!state.messages.length) { toast('当前对话为空'); return; }
    var title = $('topTitle').textContent || '医学知识问答';
    var md = '# ' + title + '\n\n> 由 Dify 应用「问答5.0」生成 · ' + new Date().toLocaleString('zh-CN') +
      '\n> 用户：' + (state.user ? state.user.username : '') +
      '\n> 免责声明：内容仅供医学知识参考，不能替代执业医师的诊断与治疗。\n\n---\n\n';
    state.messages.forEach(function (m) {
      md += (m.role === 'user' ? '### 我\n\n' : '### 医学助手\n\n') + m.content + '\n\n';
      if (m.meta && m.meta.files && m.meta.files.length) {
        md += '**上传的知识文件**：' + m.meta.files.map(function (f) { return f.name; }).join('、') + '\n\n';
      }
      if (m.meta && m.meta.ingest) md += '**处理方式**：知识文件自动入库\n\n';
      if (m.meta && m.meta.kb) md += '**知识来源**（命中 ' + m.meta.kb + ' 条）\n\n';
      if (m.sources && m.sources.length) {
        m.sources.forEach(function (r) {
          md += '- ' + (r.name || '片段') + (r.dataset ? '（' + r.dataset + '）' : '') +
            (typeof r.score === 'number' ? ' 相关度 ' + r.score.toFixed(3) : '') + '\n';
        });
        md += '\n';
      }
      md += '---\n\n';
    });
    var blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = title.replace(/[\\/:*?"<>|]/g, '_').slice(0, 40) + '.md';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 4000);
    toast('已导出 Markdown');
  }

  /* ==================== 登录 / 注册 ==================== */
  function setAuthMode(mode) {
    state.authMode = mode;
    $('authTabs').setAttribute('data-tab', mode);
    Array.prototype.forEach.call($('authTabs').querySelectorAll('.tab'), function (t) {
      t.classList.toggle('active', t.dataset.tab === mode);
    });
    $('confirmField').hidden = mode !== 'register';
    $('authSubmit').querySelector('.btn-text').textContent = mode === 'register' ? '注 册' : '登 录';
    $('password').setAttribute('autocomplete', mode === 'register' ? 'new-password' : 'current-password');
    $('authError').hidden = true;
    $('username').value = ''; $('password').value = ''; $('confirm').value = '';
  }
  function authError(msg) {
    var el = $('authError'); el.textContent = msg; el.hidden = false;
  }
  function showAuth() {
    $('app').hidden = true;
    $('authView').hidden = false;
    $('authView').style.display = 'flex';
  }
  function showApp() {
    $('authView').style.display = 'none';
    $('authView').hidden = true;
    $('app').hidden = false;
    $('userName').textContent = state.user ? state.user.username : '—';
  }

  async function submitAuth(e) {
    e.preventDefault();
    var username = $('username').value.trim();
    var password = $('password').value;
    var confirm = $('confirm').value;
    var isReg = state.authMode === 'register';
    $('authError').hidden = true;

    if (!username) return authError('请输入用户名');
    if (isReg && !/^[A-Za-z0-9_\u4e00-\u9fa5]{2,20}$/.test(username)) return authError('用户名需为 2-20 位中文、字母、数字或下划线');
    if (!password) return authError('请输入密码');
    if (password.length < 6) return authError('密码至少 6 位');
    if (isReg && password !== confirm) return authError('两次输入的密码不一致');

    var btn = $('authSubmit');
    btn.disabled = true; btn.querySelector('.spinner').hidden = false;
    try {
      var res = await fetch(isReg ? '/api/register' : '/api/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username, password: password })
      });
      var data = await res.json().catch(function () { return {}; });
      if (!res.ok || !data.ok) throw new Error(data.error || '操作失败，请稍后再试');

      state.token = data.token;
      state.user = data.user;
      localStorage.setItem(LS_TOKEN, data.token);
      showApp();
      await loadConversations();
      if (state.conversations.length) await openConversation(state.conversations[0].id);
      else { state.currentId = null; renderWelcome(); }
      renderSessions();
      toast(isReg ? '注册成功，欢迎使用' : '登录成功');
      healthCheck();
      setTimeout(function () { $('q').focus(); }, 120);
    } catch (err) {
      authError(err.message || '网络异常');
    } finally {
      btn.disabled = false; btn.querySelector('.spinner').hidden = true;
    }
  }

  async function logout() {
    if (state.busy) { toast('正在回答中，请先停止', true); return; }
    try { await api('/api/logout', { method: 'POST' }); } catch (e) {}
    state.token = ''; state.user = null;
    state.conversations = []; state.currentId = null; state.messages = [];
    state.attachments = []; state.uploading = 0;
    localStorage.removeItem(LS_TOKEN);
    setAuthMode('login');
    showAuth();
    toast('已退出登录');
  }

  async function restoreSession() {
    if (!state.token) { setAuthMode('login'); showAuth(); return; }
    try {
      var res = await fetch('/api/me', { headers: { 'Authorization': 'Bearer ' + state.token } });
      if (!res.ok) throw new Error('invalid');
      var data = await res.json();
      state.user = data.user;
      showApp();
      await loadConversations();
      if (state.conversations.length) await openConversation(state.conversations[0].id);
      else { renderWelcome(); $('topTitle').textContent = '新对话'; }
      healthCheck();
    } catch (e) {
      state.token = '';
      localStorage.removeItem(LS_TOKEN);
      setAuthMode('login');
      showAuth();
    }
  }

  /* ==================== 事件绑定 ==================== */
  function bind() {
    Array.prototype.forEach.call($('authTabs').querySelectorAll('.tab'), function (t) {
      t.addEventListener('click', function () { setAuthMode(t.dataset.tab); $('username').focus(); });
    });
    $('authForm').addEventListener('submit', submitAuth);
    $('pwdEye').addEventListener('click', function () {
      var p = $('password'), show = p.type === 'password';
      p.type = show ? 'text' : 'password';
      $('pwdEye').classList.toggle('on', show);
    });

    $('q').addEventListener('input', autoGrow);
    $('q').addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
    });
    $('send').addEventListener('click', function () {
      if (state.busy) { if (state.controller) state.controller.abort(); return; }
      send();
    });

    // ---- 知识文件：选择 / 拖拽 ----
    $('btnAttach').addEventListener('click', function () { $('fileInput').click(); });
    $('fileInput').addEventListener('change', function () {
      uploadFiles($('fileInput').files);
      $('fileInput').value = '';
    });
    $('filesBar').addEventListener('click', function (e) {
      var rm = e.target && e.target.closest ? e.target.closest('[data-rm]') : null;
      if (!rm) return;
      var i = parseInt(rm.getAttribute('data-rm'), 10);
      state.attachments.splice(i, 1);
      renderFilesBar();
    });
    ['dragenter', 'dragover'].forEach(function (ev) {
      $('box').addEventListener(ev, function (e) {
        if (e.dataTransfer && e.dataTransfer.types && Array.prototype.indexOf.call(e.dataTransfer.types, 'Files') >= 0) {
          e.preventDefault();
          $('box').classList.add('drop');
        }
      });
    });
    ['dragleave', 'drop'].forEach(function (ev) {
      $('box').addEventListener(ev, function (e) {
        $('box').classList.remove('drop');
        if (ev === 'drop' && e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
          e.preventDefault();
          uploadFiles(e.dataTransfer.files);
        }
      });
    });

    $('newChat').addEventListener('click', function () {
      if (state.busy) { toast('请先停止当前回答'); return; }
      var cur = state.conversations.filter(function (c) { return c.id === state.currentId; })[0];
      if (cur && state.messages.length === 0) { toast('已在新对话中'); renderWelcome(); $('q').focus(); return; }
      newConversation(false);
    });

    $('hamb').addEventListener('click', function () { $('side').classList.toggle('on'); });
    $('btnLogout').addEventListener('click', logout);
    $('btnKb').addEventListener('click', function () {
      if (window.KB) window.KB.open();
      if (window.innerWidth <= 860) $('side').classList.remove('on');
    });
    $('kbMask').addEventListener('click', function () { if (window.KB) window.KB.close(); });
    $('btnExport').addEventListener('click', exportCurrent);
    $('btnTheme').addEventListener('click', function () {
      var cur = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      applyTheme(cur);
      toast(cur === 'dark' ? '已切换为深色模式' : '已切换为浅色模式');
    });

    $('btnSetting').addEventListener('click', openDrawer);
    $('btnSetting2').addEventListener('click', openDrawer);
    $('drClose').addEventListener('click', closeDrawer);
    $('mask').addEventListener('click', closeDrawer);
    $('stat').addEventListener('click', healthCheck);
    $('cfgType').addEventListener('change', toggleInputVar);
    $('quickBases').addEventListener('click', function (e) {
      var b = e.target && e.target.closest ? e.target.closest('.qbtn') : null;
      if (b) { $('cfgBase').value = b.getAttribute('data-base'); renderQuickBases(b.getAttribute('data-base')); }
    });
    $('btnSave').addEventListener('click', saveSettings);
    $('btnReset').addEventListener('click', function () {
      $('cfgBase').value = 'http://127.0.0.1/v1';
      $('cfgKey').value = '';
      $('cfgType').value = 'chatflow';
      $('cfgInputVar').value = 'query';
      $('cfgInputs').value = '{}';
      $('cfgStream').checked = true;
      $('cfgTimeout').value = 900;
      renderQuickBases('http://127.0.0.1/v1');
      toggleInputVar();
      toast('已填回默认值，记得点「保存并重连」');
    });

    window.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        closeDrawer();
        if (window.KB) window.KB.close();
      }
    });
  }

  /* ==================== 初始化 ==================== */
  (function init() {
    var th = 'light';
    try { th = localStorage.getItem(LS_THEME) || 'light'; } catch (e) {}
    applyTheme(th);
    bind();
    autoGrow();
    renderFilesBar();
    restoreSession();
  })();

})();
