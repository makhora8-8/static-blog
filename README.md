# static-blog

Zero-dependency Markdown-to-static-blog generator (`build_blog.py`).

## Usage

Put posts in `posts/`, each file starts with a header:

```markdown
---
title: Post title
date: 2024-01-15
---

Body in Markdown...
```

Then run:

```sh
python3 build_blog.py
```

Outputs `posts/<name>.html` per post and an `index.html` homepage sorted by
date (newest first). Pages are dependency-free and support dark mode
(auto-follows the system, with a manual toggle saved in localStorage).

Header may also be plain `key: value` lines; dates accept `2024/01/15`,
`Jan 15, 2024`, ISO datetimes, etc. Posts missing `title`/`date` are skipped
with a warning.

Supported Markdown subset: fenced code blocks, ATX headings, bullet/numbered
lists, inline code / bold / italic / links, blank-line paragraphs.
