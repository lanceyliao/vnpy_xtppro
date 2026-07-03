#!/usr/bin/env python3
"""批量抓取 XTP Pro 6个官方文档，提取纯文本存本地 .txt"""

import re, html, urllib.request, urllib.error, time, sys

DOCS = [
    ("doc1_quickstart.txt",
     "https://xtp.zts.com.cn/xtp-pro/API4/%E8%A1%8C%E6%83%85XQuote-API%E4%BD%BF%E7%94%A8%E6%8C%87%E5%8D%97QuickStart.html"),
    ("doc2_xtp_to_xtppro_changes.txt",
     "https://xtp.zts.com.cn/xtp-pro/API4/%E4%BB%8EXTP%E8%A1%8C%E6%83%85%E5%88%B0XTP-Pro%E8%A1%8C%E6%83%85API%E7%9A%84%E5%8F%98%E5%8C%96/%E4%BB%8EXTP%E8%A1%8C%E6%83%85%E5%88%B0XTP-Pro%E8%A1%8C%E6%83%85API%E7%9A%84%E5%8F%98%E5%8C%96.html"),
    ("doc3_disconnect_handling.txt",
     "https://xtp.zts.com.cn/xtp-pro/API4/%E8%A1%8C%E6%83%85XQuote-API%E6%96%AD%E7%BA%BF%E5%90%8E%E5%BA%94%E5%AF%B9%E6%8E%AA%E6%96%BD.html"),
    ("doc4_old_config_params.txt",
     "https://xtp.zts.com.cn/xtp-pro/API4/Pro%E8%A1%8C%E6%83%85%E6%97%A7%E7%89%88%E9%85%8D%E7%BD%AE%E6%96%87%E4%BB%B6%E5%8F%82%E6%95%B0%E8%AF%B4%E6%98%8E.html"),
    ("doc5_onboarding_guide.txt",
     "https://xtp.zts.com.cn/xtp-pro/API4/%E8%A1%8C%E6%83%85%E6%9C%8D%E5%8A%A1%E6%8E%A5%E5%85%A5%E5%89%8D%E6%8C%87%E5%BC%95/%E8%A1%8C%E6%83%85%E6%9C%8D%E5%8A%A1%E6%8E%A5%E5%85%A5%E5%89%8D%E6%8C%87%E5%BC%95.html"),
    ("doc6_faq.txt",
     "https://xtp.zts.com.cn/xtp-pro/API4/API%E5%B8%B8%E8%A7%81%E9%97%AE%E9%A2%98/API%E5%B8%B8%E8%A7%81%E9%97%AE%E9%A2%98.html"),
]

OUT_DIR = "/root/vnpy_xtppro/docs_raw"

def html_to_text(raw_html: str) -> str:
    """HTML → 纯文本，保留行号可追溯"""
    # 去掉 script/style
    text = re.sub(r'<script[^>]*>.*?</script>', '', raw_html, flags=re.S)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.S)
    # <br> / <p> / <div> / <li> / <h1-6> → 换行
    text = re.sub(r'<br\s*/?>',  '\n', text, flags=re.I)
    text = re.sub(r'</(p|div|li|h[1-6]|tr|dt|dd|pre|blockquote)>', '\n', text, flags=re.I)
    # 其余标签删除
    text = re.sub(r'<[^>]+>', '', text)
    # HTML 实体
    text = html.unescape(text)
    # 合并连续空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    # 每行 strip
    lines = [l.strip() for l in text.splitlines()]
    # 去掉开头导航（到第一个实质标题）
    # 保留所有行，带行号
    return '\n'.join(lines)

for fname, url in DOCS:
    print(f"Fetching {fname} ...", end=" ", flush=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        text = html_to_text(raw)
        outpath = f"{OUT_DIR}/{fname}"
        with open(outpath, "w", encoding="utf-8") as f:
            f.write(text)
        nlines = text.count('\n') + 1
        print(f"OK ({nlines} lines)")
    except Exception as e:
        print(f"FAIL: {e}")
    time.sleep(0.5)

print("\nAll done.")
