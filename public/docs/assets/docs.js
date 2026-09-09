/* ------------------------------------------------------------------
   AB-Verified documentation site: theme, navigation, search, diagrams.
   No build step, no framework. Works from file:// as well as a server.
   ------------------------------------------------------------------ */
(function () {
  'use strict';

  var root = document.documentElement;
  var BASE = root.getAttribute('data-base') || '';

  /* ---------- theme ---------------------------------------------- */
  function currentTheme() {
    return root.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  }
  function setTheme(t) {
    root.setAttribute('data-theme', t);
    try { localStorage.setItem('abv-docs-theme', t); } catch (e) { /* private mode */ }
    var btn = document.getElementById('theme-btn');
    if (btn) {
      btn.textContent = t === 'light' ? 'Dark' : 'Light';
      btn.setAttribute('aria-label', 'Switch to the ' + (t === 'light' ? 'dark' : 'light') + ' theme');
    }
    renderDiagrams(true);
  }
  var themeBtn = document.getElementById('theme-btn');
  if (themeBtn) {
    themeBtn.textContent = currentTheme() === 'light' ? 'Dark' : 'Light';
    themeBtn.addEventListener('click', function () {
      setTheme(currentTheme() === 'light' ? 'dark' : 'light');
    });
  }

  /* ---------- mobile navigation ----------------------------------- */
  var sidebar = document.getElementById('sidebar');
  var scrim = document.getElementById('scrim');
  var menuBtn = document.getElementById('menu-btn');
  function closeNav() {
    if (sidebar) sidebar.classList.remove('open');
    if (scrim) scrim.classList.remove('on');
  }
  if (menuBtn && sidebar && scrim) {
    menuBtn.addEventListener('click', function () {
      sidebar.classList.toggle('open');
      scrim.classList.toggle('on');
    });
    scrim.addEventListener('click', closeNav);
  }

  /* ---------- copy buttons ---------------------------------------- */
  Array.prototype.forEach.call(document.querySelectorAll('.copy'), function (btn) {
    btn.addEventListener('click', function () {
      var block = btn.closest('.code, figure.diagram');
      if (!block) return;
      var src = block.querySelector('pre');
      var text = src ? (src.dataset.src || src.innerText) : '';
      var done = function () {
        var was = btn.textContent;
        btn.textContent = 'Copied';
        setTimeout(function () { btn.textContent = was; }, 1400);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text, done); });
      } else {
        fallbackCopy(text, done);
      }
    });
  });
  function fallbackCopy(text, done) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); done(); } catch (e) { /* clipboard unavailable */ }
    document.body.removeChild(ta);
  }

  /* ---------- expand a diagram to the full window ------------------ */
  var overlay = null;
  function diagramOverlay() {
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.className = 'd-overlay';
    overlay.innerHTML =
      '<div class="o-bar"><span class="o-ttl"></span><span class="grow"></span>' +
      '<button class="expand" type="button">Close</button></div><div class="o-body"></div>';
    overlay.querySelector('button').addEventListener('click', closeOverlay);
    document.body.appendChild(overlay);
    return overlay;
  }
  function closeOverlay() {
    if (overlay) {
      overlay.classList.remove('on');
      overlay.querySelector('.o-body').innerHTML = '';
    }
  }
  Array.prototype.forEach.call(document.querySelectorAll('figure.diagram .expand'), function (btn) {
    btn.addEventListener('click', function () {
      var figure = btn.closest('figure.diagram');
      var svg = figure && figure.querySelector('svg');
      if (!svg) return;
      var box = diagramOverlay();
      box.querySelector('.o-ttl').textContent = figure.querySelector('.lang').textContent;
      var body = box.querySelector('.o-body');
      body.innerHTML = '';
      var copy = svg.cloneNode(true);
      copy.removeAttribute('width');
      copy.style.maxWidth = '100%';
      body.appendChild(copy);
      box.classList.add('on');
    });
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeOverlay();
  });

  /* ---------- mermaid diagrams ------------------------------------ */
  var mermaidLib = null;
  var mermaidPending = null;
  var diagrams = document.querySelectorAll('.mermaid');

  function themeVars() {
    var dark = currentTheme() === 'dark';
    var line = dark ? '#5c7495' : '#9aa7b6';
    return {
      theme: 'base',
      darkMode: dark,
      fontFamily: 'Roboto, Arial, "Helvetica Neue", sans-serif',
      fontSize: '14px',
      themeVariables: {
        background: dark ? '#15263d' : '#ffffff',
        primaryColor: dark ? '#22384f' : '#eef3f8',
        primaryTextColor: dark ? '#ffffff' : '#1a2e4a',
        primaryBorderColor: dark ? '#29ccff' : '#0f6394',
        secondaryColor: dark ? '#22364f' : '#f2f2f2',
        tertiaryColor: dark ? '#15263d' : '#f7f8f9',
        lineColor: line,
        textColor: dark ? '#e8eef5' : '#1a2e4a',
        mainBkg: dark ? '#22384f' : '#eef3f8',
        nodeBorder: dark ? '#29ccff' : '#0f6394',
        clusterBkg: dark ? 'rgba(255,255,255,0.04)' : '#f7f8f9',
        clusterBorder: line,
        titleColor: dark ? '#ffffff' : '#1a2e4a',
        edgeLabelBackground: dark ? '#15263d' : '#ffffff',
        actorBkg: dark ? '#22384f' : '#eef3f8',
        actorBorder: dark ? '#29ccff' : '#0f6394',
        actorTextColor: dark ? '#ffffff' : '#1a2e4a',
        signalColor: dark ? '#e8eef5' : '#1a2e4a',
        signalTextColor: dark ? '#e8eef5' : '#1a2e4a',
        labelBoxBkgColor: dark ? '#22384f' : '#eef3f8',
        labelBoxBorderColor: dark ? '#29ccff' : '#0f6394',
        labelTextColor: dark ? '#ffffff' : '#1a2e4a',
        loopTextColor: dark ? '#e8eef5' : '#1a2e4a',
        noteBkgColor: dark ? '#0f6394' : '#e6f1f8',
        noteTextColor: dark ? '#ffffff' : '#1a2e4a',
        noteBorderColor: dark ? '#29ccff' : '#0f6394',
        sectionBkgColor: dark ? '#1c2f47' : '#f2f2f2',
        sectionBkgColor2: dark ? '#22384f' : '#e9e9e9',
        altSectionBkgColor: dark ? '#15263d' : '#ffffff',
        taskBkgColor: dark ? '#22384f' : '#dbe6f0',
        taskTextColor: dark ? '#ffffff' : '#1a2e4a',
        taskTextOutsideColor: dark ? '#e8eef5' : '#1a2e4a',
        taskTextDarkColor: '#1a2e4a',
        gridColor: line,
        todayLineColor: '#fa0d0d'
      }
    };
  }

  function showRaw() {
    Array.prototype.forEach.call(diagrams, function (el) {
      if (el.querySelector('svg')) return;
      el.classList.add('raw');
      el.textContent = el.dataset.src || el.textContent;
    });
  }

  function renderDiagrams(rerender) {
    if (!diagrams.length) return;
    if (!mermaidLib) {
      if (mermaidPending) return;
      mermaidPending = import('https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs')
        .then(function (mod) {
          mermaidLib = mod.default;
          mermaidPending = null;
          draw();
        })
        .catch(function () {
          mermaidPending = null;
          showRaw();
        });
      return;
    }
    if (rerender) draw();
  }

  function draw() {
    var cfg = themeVars();
    cfg.startOnLoad = false;
    cfg.securityLevel = 'strict';
    cfg.flowchart = { htmlLabels: true, curve: 'basis', useMaxWidth: true };
    cfg.sequence = { useMaxWidth: true, wrap: true, width: 170 };
    cfg.gantt = { useMaxWidth: true };
    mermaidLib.initialize(cfg);
    Array.prototype.forEach.call(diagrams, function (el) {
      el.removeAttribute('data-processed');
      el.classList.remove('raw');
      el.textContent = el.dataset.src || el.textContent;
    });
    mermaidLib.run({ nodes: diagrams }).catch(showRaw);
  }

  Array.prototype.forEach.call(diagrams, function (el) {
    if (!el.dataset.src) el.dataset.src = el.textContent;
  });
  renderDiagrams(false);

  /* ---------- scroll spy ------------------------------------------ */
  var railLinks = document.querySelectorAll('.rail a[href^="#"], .sidebar .subs a[href^="#"]');
  if (railLinks.length && 'IntersectionObserver' in window) {
    var byId = {};
    Array.prototype.forEach.call(railLinks, function (a) {
      var id = a.getAttribute('href').slice(1);
      (byId[id] = byId[id] || []).push(a);
    });
    var heads = Array.prototype.filter.call(
      document.querySelectorAll('.prose h2[id], .prose h3[id]'),
      function (h) { return byId[h.id]; }
    );
    var visible = {};
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { visible[e.target.id] = e.isIntersecting; });
      var active = null;
      for (var i = 0; i < heads.length; i++) {
        if (visible[heads[i].id]) { active = heads[i].id; break; }
      }
      if (!active) {
        for (var j = heads.length - 1; j >= 0; j--) {
          if (heads[j].getBoundingClientRect().top < 140) { active = heads[j].id; break; }
        }
      }
      Array.prototype.forEach.call(railLinks, function (a) { a.classList.remove('here'); });
      if (active && byId[active]) byId[active].forEach(function (a) { a.classList.add('here'); });
    }, { rootMargin: '-70px 0px -72% 0px', threshold: 0 });
    heads.forEach(function (h) { spy.observe(h); });
  }

  /* ---------- search ---------------------------------------------- */
  var box = document.getElementById('search-input');
  var panel = document.getElementById('search-results');
  if (box && panel) {
    var index = window.DOCS_SEARCH || [];
    var hits = [];
    var cursor = -1;

    function esc(s) {
      return s.replace(/[&<>"]/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
      });
    }

    function snippet(text, terms) {
      var lower = text.toLowerCase();
      var at = -1;
      for (var i = 0; i < terms.length && at < 0; i++) at = lower.indexOf(terms[i]);
      if (at < 0) at = 0;
      var from = Math.max(0, at - 42);
      var cut = text.slice(from, from + 168);
      var out = esc((from > 0 ? '…' : '') + cut + (from + 168 < text.length ? '…' : ''));
      terms.forEach(function (t) {
        if (!t) return;
        out = out.replace(new RegExp('(' + t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'ig'), '<mark>$1</mark>');
      });
      return out;
    }

    function score(entry, terms) {
      var title = entry.h.toLowerCase();
      var page = entry.t.toLowerCase();
      var body = entry.x.toLowerCase();
      var total = 0;
      for (var i = 0; i < terms.length; i++) {
        var t = terms[i];
        var s = 0;
        if (title.indexOf(t) === 0) s = 26;
        else if (title.indexOf(t) > -1) s = 18;
        else if (page.indexOf(t) > -1) s = 8;
        if (body.indexOf(t) > -1) s += 6;
        if (!s) return 0;
        total += s;
      }
      return total;
    }

    function render(query) {
      var terms = query.toLowerCase().split(/\s+/).filter(Boolean);
      hits = [];
      cursor = -1;
      if (!terms.length) {
        panel.classList.remove('open');
        panel.innerHTML = '';
        return;
      }
      var scored = [];
      for (var i = 0; i < index.length; i++) {
        var s = score(index[i], terms);
        if (s) scored.push({ e: index[i], s: s });
      }
      scored.sort(function (a, b) { return b.s - a.s; });
      hits = scored.slice(0, 9).map(function (r) { return r.e; });
      if (!hits.length) {
        panel.innerHTML = '<div class="r-empty">No matches for &ldquo;' + esc(query) + '&rdquo;.</div>';
        panel.classList.add('open');
        return;
      }
      panel.innerHTML = hits.map(function (e) {
        return '<a href="' + BASE + e.p + e.a + '">' +
          '<div class="r-crumb">' + esc(e.t) + '</div>' +
          '<div class="r-title">' + esc(e.h) + '</div>' +
          '<div class="r-snip">' + snippet(e.x, terms) + '</div></a>';
      }).join('');
      panel.classList.add('open');
    }

    var move = function (delta) {
      var links = panel.querySelectorAll('a');
      if (!links.length) return;
      if (cursor > -1 && links[cursor]) links[cursor].classList.remove('cur');
      cursor = (cursor + delta + links.length) % links.length;
      links[cursor].classList.add('cur');
      links[cursor].scrollIntoView({ block: 'nearest' });
    };

    box.addEventListener('input', function () { render(box.value.trim()); });
    box.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
      else if (e.key === 'Enter') {
        var links = panel.querySelectorAll('a');
        var target = links[cursor > -1 ? cursor : 0];
        if (target) { e.preventDefault(); window.location.href = target.getAttribute('href'); }
      } else if (e.key === 'Escape') {
        box.value = '';
        panel.classList.remove('open');
        box.blur();
      }
    });
    document.addEventListener('click', function (e) {
      if (!panel.contains(e.target) && e.target !== box) panel.classList.remove('open');
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === '/' && document.activeElement !== box && !/^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName)) {
        e.preventDefault();
        box.focus();
      }
    });
  }
})();
