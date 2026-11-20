"""改字演练：改掉目录里的文字，看有没有测试因此失败（2.0.0 S3 评审后的修复）。

    .venv\\Scripts\\python.exe -X utf8 -B verification\\i18n_drill.py [--set 编号=中文|English ...] [--keep]
                                                                    [run_tests.py 的参数，例如 --quick、-k a,b、-j 4]

"改一条界面文字只动一处"的证据：在临时副本里改文字，再在副本里跑 run_tests.py。

- 不带 --set：改掉目录里每一条文字（EXEMPT 里的除外）。中文把最后一个汉字换成"甲"（原来就是"甲"的换成"乙"），
  英文把第一个词之后的第一个 e 换成 o；中英相同的文字两边改成同一个样子；占位符不动，长度不变。
- 带 --set：只把这几条改成给出的文字（"编号=中文|English"；只给中文时英文不变）。

副本在 %TEMP%\\ytdlp200\\i18n-drill-*：复制源码目录（build 与 vendor 除外，vendor 用目录联接），所以不碰真正的目录，
也可以和真正目录里的 run_tests 同时跑。退出码就是副本里 run_tests 的退出码；--keep 保留副本。
测试失败而原因是测试抄写了某条目录文字：把那处改成 _sandbox.zh_t / en_t（或 msg），机制测试改用测试自己注入的假编号；
确实不能改的文字，把编号加进 EXEMPT 并写理由（要短，每条写清楚为什么）。
不导入 src 的模块；只用标准库。
"""
from __future__ import annotations

import argparse
import ast
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SKIP_TOP = {'build', 'vendor', '.git', '.venv'}
# 不改的编号（前缀以 . 结尾表示整组）和理由。这里的每一条都是一条规则，不是措辞。
EXEMPT = {
    'settings.language.': '语言名：读另一种语言的人要认得出自己的语言，C5 按原文钉住三条语言名',
    'common.sentence_join': '分隔符：英文句子之间的空格，没有可改的字',
    'core.proxy_mask': "存储标记：中文就是资料里存的 messages.PROXY_TOKEN '[代理]'，英文是它的显示",
}
# 只有中文不改的编号：英文照样改。
ZH_FIXED = {
    'filetext.': '资料目录里的说明文件（网页正文.txt、阅读说明.txt）：中文版要与 1.6.0 写出的字节相同，'
                 'test_i18n_ui 按 1.6.0 的写法逐字节比对，这是磁盘格式',
}
CJK = re.compile(r'[一-鿿]')


def exempt(key, table=None) -> bool:
    return any(key == name or (name.endswith('.') and key.startswith(name)) for name in (EXEMPT if table is None
                                                                                          else table))


def literal_spans(text):
    """[(start, end)] of the literal parts of a format string (placeholders and '{{' / '}}' left out)."""
    spans, index, start = [], 0, 0
    while index < len(text):
        pair = text[index:index + 2]
        if pair in ('{{', '}}'):
            spans.append((start, index))
            index += 2
            start = index
            continue
        if text[index] == '{':
            spans.append((start, index))
            index = text.find('}', index) + 1 or len(text)
            start = index
            continue
        index += 1
    spans.append((start, len(text)))
    return [(begin, end) for begin, end in spans if end > begin]


def perturbed_zh(text):
    for begin, end in reversed(literal_spans(text)):
        for index in range(end - 1, begin - 1, -1):
            if CJK.match(text[index]):
                return text[:index] + ('乙' if text[index] == '甲' else '甲') + text[index + 1:]
    return text


def perturbed_en(text):
    first_space = text.find(' ')
    for begin, end in literal_spans(text):
        for index in range(max(begin, first_space + 1 if first_space >= 0 else len(text)), end):
            if text[index] == 'e':
                return text[:index] + 'o' + text[index + 1:]
    return text


def rewrite_catalog(path, wanted):
    """Rewrite the TEXTS values of one catalog file: wanted(key, zh, en) -> (zh, en) or None.  Returns the keys
    changed."""
    source = path.read_text(encoding='utf-8')
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    edits, changed = [], []
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'TEXTS'):
            continue
        for key, value in zip(node.value.keys, node.value.values):
            zh, en = ast.literal_eval(value)
            new = wanted(key.value, zh, en)
            if new is None or new == (zh, en):
                continue
            start = offsets[value.lineno - 1] + len(lines[value.lineno - 1].encode('utf-8')[:value.col_offset]
                                                        .decode('utf-8'))
            end = offsets[value.end_lineno - 1] + len(lines[value.end_lineno - 1].encode('utf-8')[:value.end_col_offset]
                                                      .decode('utf-8'))
            edits.append((start, end, f'({new[0]!r}, {new[1]!r})'))
            changed.append(key.value)
    for start, end, text in sorted(edits, reverse=True):
        source = source[:start] + text + source[end:]
    path.write_text(source, encoding='utf-8', newline='\n')
    return changed


def make_copy() -> Path:
    base = Path(os.environ.get('TEMP', tempfile.gettempdir())) / 'ytdlp200'
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='i18n-drill-', dir=base))
    ignore = shutil.ignore_patterns('__pycache__')
    for entry in ROOT.iterdir():
        if entry.name in SKIP_TOP:
            continue
        if entry.is_dir():
            shutil.copytree(entry, work / entry.name, ignore=ignore)
        else:
            shutil.copy2(entry, work / entry.name)
    if (ROOT / 'vendor').is_dir():
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(work / 'vendor'), str(ROOT / 'vendor')], check=True,
                       stdout=subprocess.DEVNULL)
    return work


def remove_copy(work):
    vendor = work / 'vendor'
    if vendor.exists():
        os.rmdir(vendor)                  # the junction only, never the real vendor folder
    shutil.rmtree(work, ignore_errors=True)


def parse_set(items):
    found = {}
    for item in items:
        key, _, texts = item.partition('=')
        zh, _, en = texts.partition('|')
        found[key] = (zh, en or None)
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='改字演练：在临时副本里改目录文字，再跑 run_tests.py')
    parser.add_argument('--set', action='append', default=[], metavar='编号=中文|English')
    parser.add_argument('--keep', action='store_true', help='保留临时副本')
    args, rest = parser.parse_known_args(argv)
    chosen = parse_set(args.set)
    work = make_copy()
    keep = args.keep
    try:
        def wanted(key, zh, en):
            if chosen:
                if key not in chosen:
                    return None
                new_zh, new_en = chosen[key]
                return new_zh, en if new_en is None else new_en
            if exempt(key):
                return None
            if zh == en:                  # the same text in both languages stays the same in both (D13: English-only)
                return perturbed_zh(perturbed_en(zh)), perturbed_zh(perturbed_en(en))
            return (zh if exempt(key, ZH_FIXED) else perturbed_zh(zh)), perturbed_en(en)
        changed = []
        for path in sorted((work / 'src').glob('i18n_*.py')):
            changed += rewrite_catalog(path, wanted)
        missing = sorted(set(chosen) - set(changed))
        if missing:
            print(f'i18n_drill: 目录里没有这些编号，或文字没变：{missing}', flush=True)
            return 2
        print(f'i18n_drill: 改了 {len(changed)} 条文字（{"指定" if chosen else "全部，EXEMPT 除外"}）；副本 {work}', flush=True)
        command = [sys.executable, '-X', 'utf8', '-B', str(work / 'verification' / 'run_tests.py')] + rest
        code = subprocess.call(command, cwd=str(work))
        print(f'i18n_drill: run_tests 退出码 {code}', flush=True)
        if code:
            keep = True
        return code
    finally:
        if keep:
            print(f'i18n_drill: 副本保留在 {work}', flush=True)
        else:
            remove_copy(work)


if __name__ == '__main__':
    sys.exit(main())
