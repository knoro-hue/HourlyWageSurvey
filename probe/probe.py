"""到達性・robots確認（通常のrequests・正直なUA・低頻度）"""
import sys, os, json, re, hashlib, time
from urllib.parse import urlparse
import requests
sys.path.insert(0, '.')
from wage_survey.http import RobotsRules, USER_AGENT as UA
URLS = [l.strip() for l in open(sys.argv[1]) if l.strip() and not l.startswith('#')]
os.makedirs('out', exist_ok=True)
rules = {}
res = []
for u in URLS:
    p = urlparse(u); base = f"{p.scheme}://{p.netloc}"
    if base not in rules:
        try:
            r = requests.get(base + '/robots.txt', headers={'User-Agent': UA}, timeout=20)
            open('out/robots_' + p.netloc + '.txt', 'w').write(f"status={r.status_code}\n" + r.text[:5000])
            rules[base] = RobotsRules(r.text) if r.status_code == 200 else None
        except Exception as e:
            rules[base] = None
    rr = rules[base]
    ok = True if rr is None else rr.allowed(p.path + ('?' + p.query if p.query else ''))
    rec = dict(url=u, robots_allowed=ok)
    if ok:
        try:
            r = requests.get(u, headers={'User-Agent': UA, 'Accept-Language': 'ja'}, timeout=30)
            fn = hashlib.md5(u.encode()).hexdigest()[:10] + '.html'
            open('out/' + fn, 'wb').write(r.content)
            rec.update(final=r.url, status=r.status_code, size=len(r.content), file=fn)
        except Exception as e:
            rec['error'] = str(e)[:200]
    res.append(rec); print(rec, flush=True); time.sleep(4)
json.dump(res, open('out/index.json', 'w'), ensure_ascii=False, indent=1)
