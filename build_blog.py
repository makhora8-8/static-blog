#!/usr/bin/env python3
"""build_blog.py — 把 posts/*.md 构建成零依赖的静态博客。

用法:
    python3 build_blog.py

流程:
  1. 扫描 <脚本所在目录>/posts/ 下所有 .md(UTF-8,容错 BOM);
  2. 解析每篇头部元数据 title 与 date(兼容 YAML front matter 与纯文本键值行);
  3. 每篇生成 posts/<文件名>.html(最简 Markdown 渲染,见 render_markdown);
  4. 按日期倒序生成 index.html,标题链接指向 posts/<文件名>.html。

页面共享内联 CSS/JS:暗色模式自动跟随系统,可点按钮手动切换(localStorage 记忆)。
"""

from __future__ import annotations

import html
import re
import sys
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime as DateTime
from pathlib import Path
from urllib.parse import quote

# --------------------------------------------------------------------------- #
# Markdown 解析 — 行级
# --------------------------------------------------------------------------- #

# 元数据键值行:键必须是 ASCII 词且紧贴行首("## title: x" 首字符是 #,不会误认)
_META_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$")
# 围栏代码块(仅反引号,行首至多 3 空格缩进)
_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,})([^`]*)$")
_FENCE_CLOSE = re.compile(r"^ {0,3}(`{3,})[ \t]*$")
# ATX 标题
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
# 列表
_UL = re.compile(r"^ {0,3}([-*+])[ \t]+(.*)$")
_OL = re.compile(r"^ {0,3}\d{1,9}[.)][ \t]+(.*)$")


def _clean_value(value: str) -> str:
    """剥去键值行值两侧空白与成对引号。"""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1].strip()
    return value


def _scan_yaml_block(lines: list[str], start: int) -> tuple[dict[str, str], int] | None:
    """扫描从 lines[start] == '---' 开始的 YAML front matter 块。

    返回 (meta, 正文起始行号);块未闭合时返回 None(交由纯文本逻辑回退)。
    """
    meta: dict[str, str] = {}
    i = start + 1
    while i < len(lines) and lines[i].strip() not in ("---", "..."):
        m = _META_KEY.match(lines[i])
        if m:
            meta[m.group(1).lower()] = _clean_value(m.group(2))
        i += 1
    if i >= len(lines):
        return None
    return meta, i + 1


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """解析文件头部元数据,返回 (meta, 正文)。

    兼容两种写法(键名大小写不敏感,值经 _clean_value):
      - YAML front matter:首行 --- 与闭合 --- 之间的 `键: 值` 行;
      - 纯文本:文件开头(可含前导空行)连续出现的 `键: 值` 行,
        遇首个不匹配行即停——因此 `## title: xxx` 这类正文首行不会被吃掉。
    """
    lines = text.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].strip() == "---":
        res = _scan_yaml_block(lines, i)
        if res is not None:
            return res[0], "\n".join(lines[res[1]:])
    # 纯文本键值行
    meta: dict[str, str] = {}
    j = i
    while j < len(lines):
        m = _META_KEY.match(lines[j])
        if not m:
            break
        meta[m.group(1).lower()] = _clean_value(m.group(2))
        j += 1
    return meta, "\n".join(lines[j:])


_DATE_RE = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")
_STRPTIME_FORMATS = (
    "%b %d, %Y",   # Jan 15, 2024
    "%d %b %Y",    # 15 Jan 2024
    "%B %d, %Y",   # January 15, 2024
    "%d %B %Y",    # 15 January 2024
    "%Y年%m月%d日",
)


def parse_date(value: str) -> Date | None:
    """把任意常见日期写法解析为 date;失败返回 None(绝不抛异常)。"""
    s = value.strip()
    if not s:
        return None
    # YYYY-MM-DD 与 ISO datetime
    try:
        return Date.fromisoformat(s[:10])
    except ValueError:
        pass
    # 2024/01/15、2024.1.5 等
    m = _DATE_RE.search(s)
    if m:
        try:
            return Date(*map(int, m.groups()))
        except ValueError:
            pass
    # Jan 15, 2024、15 Jan 2024、2024年1月15日 等
    for fmt in _STRPTIME_FORMATS:
        try:
            return DateTime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


@dataclass
class Post:
    stem: str
    title: str
    date_text: str
    date: Date
    body_md: str


def collect_posts(posts_dir: Path) -> list[Post]:
    """扫描目录内全部 .md,解析元数据并校验;缺 title/date 或日期无效者跳过。"""
    posts: list[Post] = []
    files = sorted(
        (p for p in posts_dir.iterdir() if p.is_file() and p.suffix.lower() == ".md"),
        key=lambda p: p.name.casefold(),
    )
    for path in files:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"warning: 无法读取 {path.name}: {exc}", file=sys.stderr)
            continue
        meta, body = parse_front_matter(text)
        title = meta.get("title")
        date_text = meta.get("date")
        if not title:
            print(f"skip {path.name}: 头部缺少 title", file=sys.stderr)
            continue
        if not date_text:
            print(f"skip {path.name}: 头部缺少 date", file=sys.stderr)
            continue
        d = parse_date(date_text)
        if d is None:
            print(f"skip {path.name}: 无法解析日期 {date_text!r}", file=sys.stderr)
            continue
        posts.append(Post(path.stem, title, date_text, d, body))
    # 日期倒序;同日按文件名升序(先按文件名排,再稳定按日期倒排)
    posts.sort(key=lambda p: p.stem.casefold())
    posts.sort(key=lambda p: p.date, reverse=True)
    return posts


def _is_safe_url(url: str) -> bool:
    """只放行 http(s)/mailto 与相对路径,杜绝 javascript: 等危险 scheme。"""
    if url.startswith(("http://", "https://", "mailto:", "/", "./", "../", "#")):
        return True
    return ":" not in url


def _parse_inline(s: str) -> str:
    """在已 html.escape 的文本上做内联解析(代码、粗、斜、链接),支持一层嵌套。"""
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        # 反斜杠转义特殊字符(最小支持)
        if ch == "\\" and i + 1 < n and s[i + 1] in "*`[":
            out.append(s[i + 1])
            i += 2
            continue
        if s.startswith("**", i):
            j = s.find("**", i + 2)
            if j > i + 2:  # 非空粗体
                out.append("<strong>" + _parse_inline(s[i + 2:j]) + "</strong>")
                i = j + 2
                continue
            out.append("**")
            i += 2
            continue
        if ch == "`":
            run = 1
            while i + run < n and s[i + run] == "`":
                run += 1
            j = s.find("`" * run, i + run)
            if j != -1:
                content = s[i + run:j]
                if len(content) >= 2 and content[0] == " " and content[-1] == " ":
                    content = content[1:-1]
                out.append("<code>" + content + "</code>")
                i = j + run
                continue
            out.append("`" * run)
            i += run
            continue
        if ch == "[":
            end = s.find("]", i + 1)
            if end != -1 and end + 1 < n and s[end + 1] == "(":
                # 括号配对扫描:url 内部允许成对括号(如 javascript:alert(1))
                close = -1
                depth = 0
                k = end + 2
                while k < n:
                    if s[k] == "(":
                        depth += 1
                    elif s[k] == ")":
                        if depth == 0:
                            close = k
                            break
                        depth -= 1
                    k += 1
                if close != -1:
                    text = _parse_inline(s[i + 1:end])
                    url = s[end + 2:close].strip()
                    if _is_safe_url(url):
                        out.append(f'<a href="{url}">{text}</a>')
                    else:
                        out.append(text)  # 危险 scheme:只保留文字
                    i = close + 1
                    continue
            out.append("[")
            i += 1
            continue
        if ch == "*":
            # 找下一个不属于 "**" 的单星作闭合
            j = i + 1
            while j < n:
                if s[j] == "*":
                    if j + 1 < n and s[j + 1] == "*":
                        j += 2
                        continue
                    break
                j += 1
            if j < n and j > i + 1:  # 非空斜体
                out.append("<em>" + _parse_inline(s[i + 1:j]) + "</em>")
                i = j + 1
                continue
            out.append("*")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def render_markdown(text: str) -> str:
    """行级块解析 + 围栏状态机(无占位符)。

    代码块内容在闭围栏时整块 html.escape 输出,绝不进入标题/列表/内联管线,
    因此块内的 `**a*b**`、`# x` 等一律原样显示。
    其余块(标题/列表/段落)的内容一律:html.escape → _parse_inline。
    """
    lines = text.splitlines()
    out: list[str] = []
    para: list[str] = []
    i, n = 0, len(lines)

    def flush_para() -> None:
        if not para:
            return
        content = " ".join(ln.strip() for ln in para if ln.strip())
        out.append("<p>" + _parse_inline(html.escape(content)) + "</p>\n")
        para.clear()

    while i < n:
        line = lines[i]
        m = _FENCE_OPEN.match(line)
        if m:
            flush_para()
            fence = m.group(1)
            i += 1
            code: list[str] = []
            while i < n:
                c = _FENCE_CLOSE.match(lines[i])
                if c and len(c.group(1)) >= len(fence):
                    break
                code.append(lines[i])
                i += 1
            out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>\n")
            i += 1  # 越过闭围栏;若未闭合,此时 i 已到 EOF
            continue
        if not line.strip():
            flush_para()
            i += 1
            continue
        m = _ATX.match(line)
        if m:
            flush_para()
            level = len(m.group(1))
            content = (m.group(2) or "").strip()
            content = re.sub(r"[ \t]+#+[ \t]*$", "", content).strip()
            out.append(f"<h{level}>" + _parse_inline(html.escape(content)) + f"</h{level}>\n")
            i += 1
            continue
        mu = _UL.match(line)
        if mu is None:
            mo = _OL.match(line)
            mu2 = None
        else:
            mu2 = mu
            mo = None
        if mu2 or mo:
            flush_para()
            tag = "ul" if mu2 else "ol"
            items = [(mu2.group(2) if mu2 else mo.group(1))]
            i += 1
            while i < n:
                nxt = lines[i]
                if not nxt.strip():
                    break
                nu = _UL.match(nxt)
                no = _OL.match(nxt) if nu is None else None
                if (tag == "ul" and nu) or (tag == "ol" and no):
                    items.append(nu.group(2) if nu else no.group(1))
                    i += 1
                else:
                    break
            out.append(f"<{tag}>\n")
            for item in items:
                out.append("<li>" + _parse_inline(html.escape(item.strip())) + "</li>\n")
            out.append(f"</{tag}>\n")
            continue
        para.append(line)
        i += 1
    flush_para()
    return "".join(out)


# --------------------------------------------------------------------------- #
# 页面模板(共享内联 CSS/JS)
# --------------------------------------------------------------------------- #

CSS = """
:root{--bg:#f6f8fa;--fg:#1f2328;--muted:#59636e;--accent:#0969da;--code-bg:#eceff2;--border:#d1d9e0;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#8d959e;--accent:#4493f8;--code-bg:#161b22;--border:#3d444d;color-scheme:dark}}
html[data-theme="dark"]{--bg:#0d1117;--fg:#e6edf3;--muted:#8d959e;--accent:#4493f8;--code-bg:#161b22;--border:#3d444d;color-scheme:dark}
html[data-theme="light"]{--bg:#f6f8fa;--fg:#1f2328;--muted:#59636e;--accent:#0969da;--code-bg:#eceff2;--border:#d1d9e0;color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.7 system-ui,-apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;transition:background .2s,color .2s}
.wrap{max-width:44rem;margin:0 auto;padding:1.5rem 1.25rem 3rem}
.top{display:flex;align-items:center;justify-content:space-between;gap:.75rem;margin-bottom:1.25rem}
.top h1{margin:0;font-size:1.6rem}
.back{color:var(--muted);text-decoration:none;font-size:.95rem}
.back:hover{color:var(--accent)}
#theme{background:none;border:1px solid var(--border);color:var(--fg);border-radius:.5rem;padding:.3rem .7rem;cursor:pointer;font-size:1rem;line-height:1.2}
#theme:hover{border-color:var(--accent)}
time{color:var(--muted);font-size:.85rem;margin-right:.6rem;white-space:nowrap}
ul.posts{list-style:none;padding:0;margin:0}
ul.posts li{padding:.55rem 0;border-bottom:1px solid var(--border);display:flex;align-items:baseline;gap:.6rem;flex-wrap:wrap}
ul.posts a{color:var(--accent);text-decoration:none}
ul.posts a:hover{text-decoration:underline}
.empty{color:var(--muted)}
h1{font-size:1.9rem;margin:.4rem 0 .2rem}
p.date{margin:.2rem 0 0}
p.date time{margin-right:0}
hr{border:none;border-top:1px solid var(--border);margin:1.25rem 0}
h2{border-bottom:1px solid var(--border);padding-bottom:.3rem;margin-top:2rem}
pre{background:var(--code-bg);border:1px solid var(--border);border-radius:.5rem;padding:.75rem 1rem;overflow-x:auto;line-height:1.5}
code{background:var(--code-bg);border-radius:.3rem;padding:.1rem .35rem;font-size:.9em}
pre code{padding:0;background:none;border-radius:0}
footer{margin-top:3rem;color:var(--muted);font-size:.8rem;border-top:1px solid var(--border);padding-top:1rem}
"""

JS = """<script>
(() => {
  const root = document.documentElement;
  const btn = document.getElementById("theme");
  const KEY = "theme";
  let saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) {}
  if (saved === "light" || saved === "dark") root.dataset.theme = saved;
  const effective = () =>
    root.dataset.theme ||
    (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const label = () => {
    if (btn) btn.textContent = effective() === "dark" ? "☀ 亮色" : "🌙 暗色";
  };
  if (btn) btn.addEventListener("click", () => {
    const next = effective() === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    try { localStorage.setItem(KEY, next); } catch (e) {}
    label();
  });
  label();
})();
</script>
"""


def _page(title_text: str, header_html: str, main_html: str) -> str:
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{html.escape(title_text)}</title>\n"
        f"<style>{CSS}</style>\n</head>\n<body>\n<div class=\"wrap\">\n"
        f"<header class=\"top\">{header_html}\n"
        "<button id=\"theme\" type=\"button\" title=\"切换亮/暗主题\">🌙</button></header>\n"
        f"{main_html}\n"
        "<footer>由 build_blog.py 生成 · Generated by build_blog.py</footer>\n"
        "</div>\n" + JS + "\n</body>\n</html>\n"
    )


def render_index(posts: list[Post]) -> str:
    if posts:
        items: list[str] = ['<ul class="posts">\n']
        for p in posts:
            href = "posts/" + quote(p.stem + ".html")
            items.append(
                f'<li><a href="{href}">{html.escape(p.title)}</a>'
                f'<time datetime="{p.date.isoformat()}">{html.escape(p.date_text)}</time></li>\n'
            )
        items.append("</ul>\n")
        body = "".join(items)
    else:
        body = '<p class="empty">暂无文章 / No posts yet.</p>\n'
    return _page("Blog", "<h1>Blog</h1>", body)


def render_article(post: Post) -> str:
    main = (
        f"<h1>{html.escape(post.title)}</h1>\n"
        f'<p class="date"><time datetime="{post.date.isoformat()}">{html.escape(post.date_text)}</time></p>\n'
        "<hr>\n"
        f"{render_markdown(post.body_md)}"
    )
    header = '<a class="back" href="../index.html">← Blog 首页</a>'
    return _page(f"{post.title} · Blog", header, main)


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #

def main() -> int:
    base = Path(__file__).resolve().parent
    posts_dir = base / "posts"
    if not posts_dir.is_dir():
        print(f"warning: 未找到目录 {posts_dir},生成空索引。", file=sys.stderr)
    posts = collect_posts(posts_dir) if posts_dir.is_dir() else []
    for p in posts:
        out = posts_dir / (p.stem + ".html")
        out.write_text(render_article(p), encoding="utf-8")
        print(f"wrote {out}")
    index = base / "index.html"
    index.write_text(render_index(posts), encoding="utf-8")
    print(f"wrote {index} ({len(posts)} posts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
