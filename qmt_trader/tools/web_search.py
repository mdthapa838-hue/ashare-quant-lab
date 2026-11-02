# -*- coding: utf-8 -*-
"""
web_search.py — 联网搜索工具

由于内置 web_search 工具的 API Key 失效，本脚本提供可用的联网搜索能力：
  1. Bing 搜索（HTML 解析）
  2. DuckDuckGo（HTML 接口）
  3. 直接抓取指定 URL

用法：
    python tools/web_search.py "查询词"
    python tools/web_search.py "查询词" --engine bing --n 10
    python tools/web_search.py --fetch "https://example.com"
"""
from __future__ import annotations

import os
import sys
import re
import ssl
import json
import time
import argparse
import urllib.request
import urllib.parse
from html import unescape

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def fetch(url: str, timeout: int = 20, headers: dict | None = None) -> str | None:
    """抓取网页内容。"""
    h = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    if headers:
        h.update(headers)
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            raw = r.read()
            for enc in ("utf-8", "gbk", "gb18030", "latin-1"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"[fetch失败] {url[:80]}  {type(e).__name__}: {str(e)[:80]}")
        return None


def strip_html(s: str) -> str:
    s = re.sub(r"<script[^>]*>.*?</script>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<style[^>]*>.*?</style>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = unescape(s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


# ---------------------------------------------------------------------------
def search_bing(q: str, n: int = 10) -> list[dict]:
    """Bing 搜索。"""
    url = f"https://www.bing.com/search?q={urllib.parse.quote(q)}&setlang=zh-CN&count={n}"
    html = fetch(url)
    if not html:
        return []
    out = []
    # 匹配结果块
    for m in re.finditer(
            r'<li class="b_algo".*?<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
            r'(?:<p[^>]*>(.*?)</p>|<div class="b_caption"[^>]*>(.*?)</div>)',
            html, flags=re.S | re.I):
        link = unescape(m.group(1))
        title = strip_html(m.group(2))
        snip = strip_html(m.group(3) or m.group(4) or "")
        if link.startswith("http") and title:
            out.append({"title": title, "url": link, "snippet": snip[:400]})
        if len(out) >= n:
            break
    # 回退：宽松匹配
    if not out:
        for m in re.finditer(r'<h2[^>]*>\s*<a[^>]+href="(http[^"]+)"[^>]*>(.*?)</a>',
                             html, flags=re.S | re.I):
            out.append({"title": strip_html(m.group(2)), "url": unescape(m.group(1)),
                        "snippet": ""})
            if len(out) >= n:
                break
    return out


def search_duckduckgo(q: str, n: int = 10) -> list[dict]:
    """DuckDuckGo HTML 接口。"""
    url = "https://html.duckduckgo.com/html/"
    data = urllib.parse.urlencode({"q": q, "kl": "cn-zh"}).encode()
    try:
        req = urllib.request.Request(
            url, data=data,
            headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=20, context=CTX) as r:
            html = r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"[ddg失败] {type(e).__name__}: {str(e)[:60]}")
        return []
    out = []
    for m in re.finditer(
            r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>(.*?)</a>'
            r'.*?<a class="result__snippet"[^>]*>(.*?)</a>',
            html, flags=re.S | re.I):
        link = unescape(m.group(1))
        if "uddg=" in link:
            link = urllib.parse.unquote(link.split("uddg=")[-1].split("&")[0])
        out.append({"title": strip_html(m.group(2)), "url": link,
                    "snippet": strip_html(m.group(3))[:400]})
        if len(out) >= n:
            break
    return out


def search_baidu(q: str, n: int = 10) -> list[dict]:
    """百度搜索。"""
    url = f"https://www.baidu.com/s?wd={urllib.parse.quote(q)}&rn={n}"
    html = fetch(url)
    if not html:
        return []
    out = []
    for m in re.finditer(
            r'<h3[^>]*class="[^"]*t[^"]*"[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            html, flags=re.S | re.I):
        out.append({"title": strip_html(m.group(2)), "url": unescape(m.group(1)),
                    "snippet": ""})
        if len(out) >= n:
            break
    return out


ENGINES = {"bing": search_bing, "ddg": search_duckduckgo, "baidu": search_baidu}


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="联网搜索工具")
    ap.add_argument("query", nargs="*", help="搜索关键词")
    ap.add_argument("--engine", default="bing", choices=list(ENGINES))
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--fetch", help="直接抓取URL并输出正文")
    ap.add_argument("--max-chars", type=int, default=6000)
    args = ap.parse_args()

    if args.fetch:
        html = fetch(args.fetch)
        if html:
            txt = strip_html(html)
            print(txt[:args.max_chars])
        return 0

    q = " ".join(args.query)
    if not q:
        print("请提供查询词，或用 --fetch URL")
        return 1

    print(f"引擎: {args.engine}")
    print(f"查询: {q}")
    print("=" * 100)
    res = ENGINES[args.engine](q, args.n)
    if not res:
        print("无结果（可能被反爬拦截，换个引擎试试）")
        return 1
    for i, r in enumerate(res, 1):
        print(f"\n[{i}] {r['title']}")
        print(f"    {r['url']}")
        if r["snippet"]:
            print(f"    {r['snippet'][:300]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
