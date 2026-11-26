/* Indeed「今見ているページを追加」ボタン（ブックマークレット本体）
 * 担当者が Indeed で普通に検索・ページ送りし、表示中の検索結果をボタンで書き写すだけ。
 * 自分でページを開いたり送ったりはしない（Indeed へ追加の通信はしない）。
 * data/manual 用の CSV を作る。
 */
(async () => {
  const STORES = __STORES__;
  const BRANDS = ['ヨドバシ', 'ビックカメラ', 'コジマ', 'ソフマップ', 'エディオン', 'EDION', '100満ボルト', 'ジョーシン', 'Joshin', '上新電機',
    'ケーズデンキ', 'ヤマダ', 'テックランド', 'ベスト電器', 'ノジマ', 'PC DEPOT', 'ピーシーデポ', 'パソコン工房', 'ユニットコム', 'グッドウィル',
    'ツクモ', 'TSUKUMO', 'じゃんぱら', 'イオシス', 'PCワンズ', 'アーク', 'ドスパラ', 'サードウェーブ'];
  const DIRECT_WORDS = ['家電量販', '家電販売', '家電製品', '家電売場', 'パソコン販売', 'PC販売', 'PCショップ', 'パソコンショップ', 'パソコン専門店'];
  const OWN_WORDS = ['ドスパラ', 'サードウェーブ'];

  if (!/indeed\./.test(location.host)) { alert('Indeed（jp.indeed.com）のページを開いてから、このボタンを押してください。'); return; }
  if (window.__iwc) { window.__iwc.style.display = 'block'; return; }

  const N = (s) => String(s || '').normalize('NFKC').replace(/\s+/g, ' ').trim();
    const today = new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);
  const KEY = 'iwc_rows_v1';
  let rows = [];
  try { rows = JSON.parse(localStorage.getItem(KEY) || '[]'); } catch (e) { rows = []; }
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(rows)); } catch (e) { /* 保存できなくても続行 */ } };

  // ---- 画面 ----
  const box = document.createElement('div');
  window.__iwc = box;
  box.style.cssText = 'position:fixed;top:12px;right:12px;z-index:2147483647;width:380px;max-height:92vh;overflow:auto;background:#fff;color:#222;border:1px solid #2557a7;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.25);font:13px/1.5 sans-serif;padding:12px';
  const opts = STORES.map((s) => `<option value="${s.id}">${s.name}</option>`).join('');
  box.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
      <b style="font-size:14px">時給調査：Indeed 書き写し</b>
      <button data-a="close" style="border:0;background:none;font-size:18px;cursor:pointer">×</button>
    </div>
    <div style="font-size:12px;color:#555;margin-bottom:6px">Indeed で普通に検索して、表示中の検索結果を「このページを追加」で書き写します。次のページは自分で開いてから、もう一度押してください。</div>
    <label>どの店舗の分か</label>
    <select data-f="store" style="width:100%">${opts}</select>
    <label>種類</label>
    <select data-f="mode" style="width:100%">
      <option value="direct">③直接競合（家電量販・PC関連）</option>
      <option value="indirect">②間接競合（周辺のアルバイト全般）</option>
    </select>
    <div style="margin:8px 0;display:flex;gap:6px;flex-wrap:wrap">
      <button data-a="add" style="background:#2557a7;color:#fff;border:0;border-radius:6px;padding:8px 14px;cursor:pointer;font-weight:bold">このページを追加</button>
      <button data-a="dl" style="background:#1a7f37;color:#fff;border:0;border-radius:6px;padding:8px 14px;cursor:pointer">CSVを保存</button>
      <button data-a="clear">集めたデータを消す</button>
    </div>
    <div data-f="count" style="font-weight:bold"></div>
    <div data-f="log" style="white-space:pre-wrap;font-size:12px;color:#555;max-height:220px;overflow:auto;border-top:1px solid #eee;margin-top:6px;padding-top:6px"></div>`;
  document.body.appendChild(box);
  const $ = (f) => box.querySelector(`[data-f="${f}"]`);
  try { const last = localStorage.getItem('iwc_last_store'); if (last) $('store').value = last; } catch (e) { /* なし */ }
  $('store').onchange = () => { try { localStorage.setItem('iwc_last_store', $('store').value); } catch (e) { /* なし */ } };
  const log = (t) => { const l = $('log'); l.textContent = `${new Date().toLocaleTimeString()} ${t}\n` + l.textContent; };
  const count = () => { $('count').textContent = `集めた求人：${rows.length} 件（CSVを保存してダッシュボードで取り込み）`; };
  count();

  // ---- 解析 ----
  function wage(text, ex) {
    const t = N(text);
    let type = /時給/.test(t) ? '時給' : /日給/.test(t) ? '日給' : /月給/.test(t) ? '月給' : /年収|年俸/.test(t) ? '年収' : '';
    if (!type && ex && ex.type) type = { hourly: '時給', daily: '日給', monthly: '月給', yearly: '年収' }[ex.type] || '';
    const nums = [];
    for (const m of t.matchAll(/(\d+(?:\.\d+)?)\s*万|(\d[\d,]*)\s*円/g)) {
      nums.push(m[1] ? Math.round(parseFloat(m[1]) * 10000) : parseInt(m[2].replace(/,/g, ''), 10));
    }
    let lo = nums[0], hi = nums[1];
    if (lo == null && ex) { lo = ex.min; hi = ex.max && ex.max !== ex.min ? ex.max : undefined; }
    return { type: type || '時給', lo: lo ?? '', hi: hi ?? '', text: t };
  }

  function mapResults(res) {
    return res.map((j) => ({
      key: j.jobkey, title: N(j.displayTitle || j.title), company: N(j.company), loc: N(j.formattedLocation),
      sal: N((j.salarySnippet || {}).text), ex: j.extractedSalary, snippet: N(String(j.snippet || '').replace(/<[^>]+>/g, ' ')),
      jt: (j.jobTypes || []).map((x) => (x && x.label) || x).join('、'),
    }));
  }
  function fromWindow() {
    try {
      const d = window.mosaic && window.mosaic.providerData && window.mosaic.providerData['mosaic-provider-jobcards'];
      const res = (((d || {}).metaData || {}).mosaicProviderJobCardsModel || {}).results;
      return res && res.length ? mapResults(res) : null;
    } catch (e) { return null; }
  }
  function fromJson(html) {
    const mark = 'window.mosaic.providerData["mosaic-provider-jobcards"]';
    const i = html.indexOf(mark);
    if (i < 0) return null;
    let s = html.slice(html.indexOf('=', i + mark.length) + 1);
    const end = s.indexOf('\n');
    s = (end > 0 ? s.slice(0, end) : s).trim().replace(/;\s*$/, '');
    try {
      const d = JSON.parse(s);
      const res = (((d || {}).metaData || {}).mosaicProviderJobCardsModel || {}).results || [];
      return res.map((j) => ({
        key: j.jobkey, title: N(j.displayTitle || j.title), company: N(j.company), loc: N(j.formattedLocation),
        sal: N((j.salarySnippet || {}).text), ex: j.extractedSalary, snippet: N(String(j.snippet || '').replace(/<[^>]+>/g, ' ')),
        jt: (j.jobTypes || []).map((x) => (x && x.label) || x).join('、'),
      }));
    } catch (e) { return null; }
  }

  function fromDom(html) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const out = [];
    doc.querySelectorAll('.job_seen_beacon, .cardOutline, li [data-jk]').forEach((c) => {
      const a = c.matches('[data-jk]') ? c : c.querySelector('[data-jk]');
      const key = a && a.getAttribute('data-jk');
      if (!key || out.some((o) => o.key === key)) return;
      const q = (sel) => N((c.querySelector(sel) || {}).textContent);
      const sal = [...c.querySelectorAll('[data-testid="attribute_snippet_testid"], .salary-snippet-container, .estimated-salary')]
        .map((e) => N(e.textContent)).find((t) => /円/.test(t)) || '';
      out.push({ key, title: q('h2 span[title], h2'), company: q('[data-testid="company-name"], .companyName'),
        loc: q('[data-testid="text-location"], .companyLocation'), sal, ex: null, snippet: q('.job-snippet, [data-testid="jobsnippet_footer"]'), jt: '' });
    });
    return out;
  }

  function classify(mode, j) {
    const t = `${j.company} ${j.title}`;
    if (OWN_WORDS.some((w) => t.includes(w))) return 'own';
    if (BRANDS.some((w) => j.company.includes(w)) || DIRECT_WORDS.some((w) => t.includes(w))) return 'direct';
    return mode === 'direct' ? 'direct' : 'indirect';
  }

  // ---- 表示中のページを書き写す ----
  function addPage() {
    const id = $('store').value;
    const s = STORES.find((x) => x.id === id);
    const mode = $('mode').value;
    const html = document.documentElement.outerHTML;
    const jobs = fromWindow() || fromJson(html) || fromDom(html);
    if (!jobs.length) { log('このページに求人の一覧が見つかりません。Indeed の検索結果のページで押してください。'); return; }
    const seen = new Set(rows.map((r) => `${r.store_id}|${r.url}`));
    let added = 0;
    for (const j of jobs) {
      const link = `${location.origin}/viewjob?jk=${j.key}`;
      const k = `${id}|${link}`;
      if (seen.has(k)) continue;
      seen.add(k);
      const w = wage(j.sal, j.ex);
      rows.push({
        store_id: id, classification: classify(mode, j), site: 'Indeed', company: j.company, shop: '', occupation: j.title,
        employment: j.jt || 'アルバイト・パート', wage_type: w.type, wage_min: w.lo, wage_max: w.hi, address: j.loc,
        work_content: [w.text, j.snippet].filter(Boolean).join(' ／ ').slice(0, 300), url: link, checked_date: today,
      });
      added++;
    }
    save(); count();
    log(`${s.name}: ${jobs.length}件中 新規${added}件を追加${added ? '' : '（このページは追加済み）'}`);
  }

  function download() {
    if (!rows.length) { alert('まだデータがありません'); return; }
    const cols = ['store_id', 'classification', 'site', 'company', 'shop', 'occupation', 'employment', 'wage_type', 'wage_min', 'wage_max',
      'address', 'work_content', 'url', 'checked_date'];
    const esc = (v) => { const s = String(v ?? ''); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const csv = '﻿' + [cols.join(','), ...rows.map((r) => cols.map((c) => esc(r[c])).join(','))].join('\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    a.download = `indeed_${today}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
  }

  box.addEventListener('click', (e) => {
    const a = e.target.getAttribute && e.target.getAttribute('data-a');
    if (!a) return;
    if (a === 'close') box.style.display = 'none';
    if (a === 'add') addPage();
    if (a === 'dl') download();
    if (a === 'clear' && confirm('集めたデータを消しますか？（CSVを保存してから消してください）')) { rows = []; save(); count(); }
  });
})();
