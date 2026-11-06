"""各求人サイトへの到達性・robots.txt可否を確認する（通常のrequests・正直なUA・低頻度）"""
import sys, os, json, re, hashlib, time
from urllib.parse import urlparse
from urllib import robotparser
import requests
UA = "HourlyWageSurveyBot/0.1 (+https://github.com/knoro-hue/hourlywagesurvey)"
URLS = [l.strip() for l in open(sys.argv[1]) if l.strip() and not l.startswith('#')]
os.makedirs('out', exist_ok=True)
rp_cache = {}
def allowed(u):
    p = urlparse(u); base = f"{p.scheme}://{p.netloc}"
    if base not in rp_cache:
        rp = robotparser.RobotFileParser()
        try:
            r = requests.get(base + "/robots.txt", headers={"User-Agent": UA}, timeout=20)
            open('out/robots_' + p.netloc + '.txt', 'w').write(r.text)
            rp.parse(r.text.splitlines())
        except Exception:
            rp = None
        rp_cache[base] = rp
    rp = rp_cache[base]
    return True if rp is None else rp.can_fetch(UA, u)
res = []
for u in URLS:
    ok = allowed(u)
    rec = dict(url=u, robots_allowed=ok)
    if ok:
        try:
            r = requests.get(u, headers={"User-Agent": UA, "Accept-Language": "ja"}, timeout=30)
            fn = hashlib.md5(u.encode()).hexdigest()[:10] + '.html'
            open('out/' + fn, 'wb').write(r.content)
            t = re.search(rb'<title>(.*?)</title>', r.content, re.S)
            rec.update(final=r.url, status=r.status_code, size=len(r.content), file=fn,
                       title=(t.group(1).decode('utf8', 'ignore')[:120] if t else ''))
        except Exception as e:
            rec['error'] = str(e)[:200]
    res.append(rec); print(rec, flush=True); time.sleep(2)
json.dump(res, open('out/index.json', 'w'), ensure_ascii=False, indent=1)
