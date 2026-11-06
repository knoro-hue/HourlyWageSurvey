"""礼儀正しいHTTP取得: 正直なUser-Agent / robots.txt遵守（ワイルドカード対応） / アクセス間隔 / リトライ"""
from __future__ import annotations

import logging
import re
import time
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

USER_AGENT = (
    "HourlyWageSurveyBot/1.0 (recruiting wage research; "
    "+https://github.com/knoro-hue/hourlywagesurvey)"
)


class RobotsRules:
    """User-agent: * グループの Allow/Disallow を * と $ 付きで評価する簡易実装"""

    def __init__(self, text: str):
        self.rules: list[tuple[bool, re.Pattern, int]] = []
        groups: list[tuple[list[str], list[tuple[str, str]]]] = []
        agents: list[str] = []
        lines: list[tuple[str, str]] = []
        last_was_agent = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k, v = k.strip().lower(), v.strip()
            if k == "user-agent":
                if not last_was_agent and agents:
                    groups.append((agents, lines))
                    agents, lines = [], []
                agents.append(v.lower())
                last_was_agent = True
            elif k in ("allow", "disallow"):
                lines.append((k, v))
                last_was_agent = False
            else:
                last_was_agent = False
        if agents:
            groups.append((agents, lines))
        for ags, ls in groups:
            if "*" in ags:
                for k, v in ls:
                    if not v:
                        continue
                    pat = re.escape(v).replace(r"\*", ".*")
                    if pat.endswith(r"\$"):
                        pat = pat[:-2] + "$"
                    self.rules.append((k == "allow", re.compile(pat), len(v)))

    def allowed(self, path_and_query: str) -> bool:
        best = None
        for allow, pat, ln in self.rules:
            if pat.match(path_and_query):
                if best is None or ln > best[1] or (ln == best[1] and allow):
                    best = (allow, ln)
        return True if best is None else best[0]


class PoliteSession:
    def __init__(self, delay_sec: float = 4.0, timeout: int = 30, max_requests: int | None = None):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ja"})
        self.delay = delay_sec
        self.timeout = timeout
        self.max_requests = max_requests
        self.count = 0
        self._last: dict[str, float] = {}
        self._robots: dict[str, RobotsRules | None] = {}

    def _robots_for(self, base: str) -> RobotsRules | None:
        if base not in self._robots:
            try:
                r = self.s.get(base + "/robots.txt", timeout=self.timeout)
                self._robots[base] = RobotsRules(r.text) if r.status_code == 200 else None
            except requests.RequestException:
                self._robots[base] = None
        return self._robots[base]

    def can_fetch(self, url: str) -> bool:
        p = urlparse(url)
        rules = self._robots_for(f"{p.scheme}://{p.netloc}")
        if rules is None:
            return True
        pq = p.path + ("?" + p.query if p.query else "")
        return rules.allowed(pq)

    def budget_left(self) -> bool:
        return self.max_requests is None or self.count < self.max_requests

    def get(self, url: str, *, check_robots: bool = True, delay: float | None = None) -> requests.Response | None:
        if check_robots and not self.can_fetch(url):
            log.warning("robots.txt により取得しません: %s", url)
            return None
        host = urlparse(url).netloc
        wait = (self.delay if delay is None else delay) - (time.time() - self._last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            try:
                self._last[host] = time.time()
                self.count += 1
                r = self.s.get(url, timeout=self.timeout)
                if r.status_code in (429, 503):
                    time.sleep(30 * (attempt + 1))
                    continue
                return r
            except requests.RequestException as e:
                log.warning("取得失敗(%s) %s: %s", attempt + 1, url, e)
                time.sleep(5 * (attempt + 1))
        return None
