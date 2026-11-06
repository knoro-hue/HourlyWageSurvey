"""window.__NUXT__ のJSを「実行せずに」解析してPythonオブジェクトへ変換する。

__NUXT__ は通常 (function(a,b,...){return {...}}(v1,v2,...)) 形式。
JSを評価せず、リテラル（オブジェクト・配列・文字列・数値）と引数参照だけを
読み取る最小パーサ。未対応の式（関数・new 等）は None として読み飛ばす。
"""
from __future__ import annotations

import json
import re

_NUXT_RE = re.compile(r"window\.__NUXT__\s*=\s*(.*?)</script>", re.S)
_IDENT_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_NUM_RE = re.compile(r"-?(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
_ESC = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


class JSParseError(ValueError):
    pass


class _Parser:
    def __init__(self, src: str, env: dict | None = None):
        self.s = src
        self.i = 0
        self.env = env or {}

    # ---- 低レベル ----
    def ws(self):
        s, n = self.s, len(self.s)
        while self.i < n:
            c = s[self.i]
            if c in " \t\r\n":
                self.i += 1
            elif s.startswith("//", self.i):
                j = s.find("\n", self.i)
                self.i = n if j < 0 else j
            elif s.startswith("/*", self.i):
                j = s.find("*/", self.i + 2)
                self.i = n if j < 0 else j + 2
            else:
                break

    def peek(self) -> str:
        self.ws()
        return self.s[self.i] if self.i < len(self.s) else ""

    def eat(self, ch: str):
        if self.peek() != ch:
            raise JSParseError(f"expected {ch!r} at {self.i}: {self.s[self.i:self.i+40]!r}")
        self.i += 1

    def ident(self) -> str:
        self.ws()
        m = _IDENT_RE.match(self.s, self.i)
        if not m:
            raise JSParseError(f"identifier expected at {self.i}")
        self.i = m.end()
        return m.group(0)

    def string(self) -> str:
        q = self.s[self.i]
        self.i += 1
        out = []
        s = self.s
        while True:
            c = s[self.i]
            if c == q:
                self.i += 1
                return "".join(out)
            if c == "\\":
                e = s[self.i + 1]
                if e == "u":
                    if s[self.i + 2] == "{":
                        j = s.index("}", self.i)
                        out.append(chr(int(s[self.i + 3:j], 16)))
                        self.i = j + 1
                        continue
                    out.append(chr(int(s[self.i + 2:self.i + 6], 16)))
                    self.i += 6
                    continue
                if e == "x":
                    out.append(chr(int(s[self.i + 2:self.i + 4], 16)))
                    self.i += 4
                    continue
                if e == "\n":
                    self.i += 2
                    continue
                out.append(_ESC.get(e, e))
                self.i += 2
                continue
            out.append(c)
            self.i += 1

    def skip_balanced(self):
        """{...} / (...) / [...] を文字列考慮で読み飛ばす"""
        pairs = {"{": "}", "(": ")", "[": "]"}
        stack = []
        s = self.s
        while self.i < len(s):
            c = s[self.i]
            if c in "\"'`":
                self.string()
                continue
            if c in pairs:
                stack.append(pairs[c])
            elif stack and c == stack[-1]:
                stack.pop()
                if not stack:
                    self.i += 1
                    return
            self.i += 1

    # ---- 値 ----
    def value(self):
        c = self.peek()
        if c == "{":
            return self.obj()
        if c == "[":
            return self.arr()
        if c in "\"'`":
            return self.string()
        if c == "!":
            self.i += 1
            return not self.value()
        if c == "-" or c == "." or c.isdigit():
            m = _NUM_RE.match(self.s, self.i)
            if not m:
                raise JSParseError(f"number expected at {self.i}")
            self.i = m.end()
            t = m.group(0)
            if t.lstrip("-").lower().startswith("0x"):
                return int(t, 16)
            v = float(t)
            return int(v) if v.is_integer() and "e" not in t.lower() and "." not in t else v
        if c == "(":
            self.i += 1
            v = self.value()
            self.eat(")")
            return v
        name = self.ident()
        if name == "true":
            return True
        if name == "false":
            return False
        if name in ("null", "undefined"):
            return None
        if name == "void":
            self.value()
            return None
        if name == "new":
            self.ident()
            while self.peek() == ".":
                self.i += 1
                self.ident()
            if self.peek() == "(":
                self.skip_balanced()
            return None
        if name == "function":
            if self.peek() != "(":
                self.ident()
            self.skip_balanced()  # params
            self.ws()
            self.skip_balanced()  # body
            return None
        v = self.env.get(name)
        # a.b / a[0] のようなメンバ参照は値として扱わない
        while self.peek() in (".", "["):
            if self.peek() == ".":
                self.i += 1
                self.ident()
            else:
                self.skip_balanced()
            v = None
        return v

    def obj(self):
        self.eat("{")
        out = {}
        while self.peek() != "}":
            c = self.peek()
            if c in "\"'":
                k = self.string()
            elif c.isdigit():
                m = _NUM_RE.match(self.s, self.i)
                self.i = m.end()
                k = m.group(0)
            else:
                k = self.ident()
            if self.peek() == ":":
                self.i += 1
                out[k] = self.value()
            else:  # shorthand {a}
                out[k] = self.env.get(k)
            if self.peek() == ",":
                self.i += 1
        self.eat("}")
        return out

    def arr(self):
        self.eat("[")
        out = []
        while self.peek() != "]":
            if self.peek() == ",":
                self.i += 1
                out.append(None)
                continue
            out.append(self.value())
            if self.peek() == ",":
                self.i += 1
        self.eat("]")
        return out


def parse_js_literal(src: str, env: dict | None = None):
    return _Parser(src, env).value()


def parse_nuxt(html: str):
    m = _NUXT_RE.search(html)
    if not m:
        return None
    src = m.group(1).strip().rstrip(";").strip()
    p = _Parser(src)
    # 先頭の括弧を外す
    while p.peek() == "(":
        p.i += 1
    p.ws()
    if not p.s.startswith("function", p.i):
        return parse_js_literal(src)
    p.i += len("function")
    p.eat("(")
    params = []
    while p.peek() != ")":
        params.append(p.ident())
        if p.peek() == ",":
            p.i += 1
    p.eat(")")
    body_start = p.i
    p.ws()
    p.skip_balanced()
    body_end = p.i
    # 末尾の (args) を読む
    if p.peek() == ")":
        p.i += 1
    p.eat("(")
    args = []
    while p.peek() != ")":
        args.append(p.value())
        if p.peek() == ",":
            p.i += 1
    env = {name: (args[k] if k < len(args) else None) for k, name in enumerate(params)}
    body = src[body_start:body_end]
    r = re.search(r"\breturn\b", body)
    if not r:
        return None
    return _Parser(body[r.end():], env).value()


if __name__ == "__main__":  # デバッグ用: python -m wage_survey.nuxt_state file.html
    import sys

    print(json.dumps(parse_nuxt(open(sys.argv[1], encoding="utf-8").read()), ensure_ascii=False)[:2000])
