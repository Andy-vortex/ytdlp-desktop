# quick
"""Fixed vectors of urls.identity and urls.link_key (2.0.0 stage S0).

    python -X utf8 -B test_identity_vectors.py

The stored keys are made by these two functions: entries.key and the pending items of a subscription,
downloads.key (the "already downloaded" memory), the archive folder names (a hash of identity) use
urls.identity; the key of an unfinished entry and of a queue item uses urls.link_key.  When either
answer changes, stored data no longer finds its own links: a downloaded item is fetched again, an
ignored or downloaded subscription item comes back, an unfinished entry is not removed after it
finished.  So the answers are pinned here, input by input, as 1.8.1 (HEAD 8d839e0) gives them:
every site with its own rule (YouTube with its hosts, short links, shorts / live / embed, lists and
mixes, tracking parameters; Bilibili with and without a part; Douyin), case in the scheme, host,
path and parameter names, ports and user info, hash-routed pages, non-ASCII and escaped links, text
that is not a link, and the inputs urlsplit refuses.

Replaces the comparison with the 1.5.1 source in test_urls.py (removed in 2.0.0 S2 with the frozen released sources).
"""
import _sandbox
import unittest

import urls

# (input, identity, link_key(playlist=False), link_key(playlist=True)) as 1.8.1 answers them.
VECTORS = (
    ('https://www.youtube.com/watch?v=dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ'),
    ('https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=abc&feature=share&t=42', 'youtube:dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ'),
    ('https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabcdef', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabcdef', 'youtube:dQw4w9WgXcQ', 'youtube:list:PLabcdef'),
    ('https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ&start_radio=1', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ&start_radio=1', 'youtube:dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ'),
    ('https://WWW.YouTube.com/watch?v=dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ'),
    ('HTTPS://www.youtube.com/watch?V=dQw4w9WgXcQ', 'https://www.youtube.com/watch?V=dQw4w9WgXcQ', 'https://www.youtube.com/watch?V=dQw4w9WgXcQ', 'https://www.youtube.com/watch?V=dQw4w9WgXcQ'),
    ('https://m.youtube.com/watch?v=dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ'),
    ('https://music.youtube.com/watch?v=dQw4w9WgXcQ', 'https://music.youtube.com/watch?v=dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ'),
    ('https://youtu.be/dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ'),
    ('https://youtu.be/dQw4w9WgXcQ?si=xyz&t=10', 'youtube:dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ'),
    ('https://youtu.be/', 'https://youtu.be/', 'https://youtu.be/', 'https://youtu.be/'),
    ('https://www.youtube.com/shorts/abcdefghijk', 'youtube:abcdefghijk', 'youtube:abcdefghijk', 'youtube:abcdefghijk'),
    ('https://www.youtube.com/live/abcdefghijk?feature=share', 'youtube:abcdefghijk', 'youtube:abcdefghijk', 'youtube:abcdefghijk'),
    ('https://www.youtube.com/playlist?list=PLabcdef', 'https://www.youtube.com/playlist?list=PLabcdef', 'youtube:list:PLabcdef', 'youtube:list:PLabcdef'),
    ('https://www.youtube.com/@channel/videos', 'https://www.youtube.com/@channel/videos', 'https://www.youtube.com/@channel/videos', 'https://www.youtube.com/@channel/videos'),
    ('https://www.youtube.com/results?search_query=lofi', 'https://www.youtube.com/results?search_query=lofi', 'https://www.youtube.com/results?search_query=lofi', 'https://www.youtube.com/results?search_query=lofi'),
    ('https://www.youtube.com/watch?v=', 'https://www.youtube.com/watch?v=', 'https://www.youtube.com/watch?v=', 'https://www.youtube.com/watch?v='),
    ('https://www.youtube.com/embed/dQw4w9WgXcQ', 'https://www.youtube.com/embed/dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ'),
    ('https://www.bilibili.com/video/BV1xx411c7mD', 'bili:BV1xx411c7mD:1', 'bili:BV1xx411c7mD:1', 'bili:BV1xx411c7mD:1'),
    ('https://www.bilibili.com/video/BV1xx411c7mD?p=3&spm_id_from=333', 'bili:BV1xx411c7mD:3', 'bili:BV1xx411c7mD:3', 'bili:BV1xx411c7mD:3'),
    ('https://m.bilibili.com/video/av170001/', 'bili:av170001:1', 'bili:av170001:1', 'bili:av170001:1'),
    ('https://b23.tv/abcdef', 'https://b23.tv/abcdef', 'https://b23.tv/abcdef', 'https://b23.tv/abcdef'),
    ('https://www.douyin.com/video/7123456789012345678', 'douyin:7123456789012345678', 'douyin:7123456789012345678', 'douyin:7123456789012345678'),
    ('https://www.douyin.com/note/7123456789012345678?previous_page=app', 'douyin:7123456789012345678', 'douyin:7123456789012345678', 'douyin:7123456789012345678'),
    ('https://v.douyin.com/abcdEF/', 'https://v.douyin.com/abcdEF/', 'https://v.douyin.com/abcdEF/', 'https://v.douyin.com/abcdEF/'),
    ('https://notbilibili.com/video/BV1xx411c7mD', 'https://notbilibili.com/video/BV1xx411c7mD', 'https://notbilibili.com/video/BV1xx411c7mD', 'https://notbilibili.com/video/BV1xx411c7mD'),
    ('https://Example.COM/Path/File.MP4?Query=Value&B=2#frag', 'https://example.com/Path/File.MP4?Query=Value&B=2', 'https://example.com/Path/File.MP4?Query=Value&B=2', 'https://example.com/Path/File.MP4?Query=Value&B=2'),
    ('https://example.com', 'https://example.com/', 'https://example.com/', 'https://example.com/'),
    ('http://example.com/a/b/', 'http://example.com/a/b/', 'http://example.com/a/b/', 'http://example.com/a/b/'),
    ('https://user:pass@Example.com:8443/x?y=1', 'https://user:pass@example.com:8443/x?y=1', 'https://user:pass@example.com:8443/x?y=1', 'https://user:pass@example.com:8443/x?y=1'),
    ('https://music.163.com/#/song?id=1', 'https://music.163.com/', 'https://music.163.com/#/song?id=1', 'https://music.163.com/#/song?id=1'),
    ('https://music.163.com/#/song?id=2', 'https://music.163.com/', 'https://music.163.com/#/song?id=2', 'https://music.163.com/#/song?id=2'),
    ('https://www.example.com/page#comments', 'https://www.example.com/page', 'https://www.example.com/page', 'https://www.example.com/page'),
    ('https://例子.example.invalid/视频/第1集?标题=中文', 'https://例子.example.invalid/视频/第1集?标题=中文', 'https://例子.example.invalid/视频/第1集?标题=中文', 'https://例子.example.invalid/视频/第1集?标题=中文'),
    ('https://xn--fsqu00a.example.invalid/%E8%A7%86%E9%A2%91', 'https://xn--fsqu00a.example.invalid/%E8%A7%86%E9%A2%91', 'https://xn--fsqu00a.example.invalid/%E8%A7%86%E9%A2%91', 'https://xn--fsqu00a.example.invalid/%E8%A7%86%E9%A2%91'),
    ('https://media.example.invalid/q?x="quoted"&y=back\\slash&z=%2F', 'https://media.example.invalid/q?x="quoted"&y=back\\slash&z=%2F', 'https://media.example.invalid/q?x="quoted"&y=back\\slash&z=%2F', 'https://media.example.invalid/q?x="quoted"&y=back\\slash&z=%2F'),
    ('https://media.example.invalid/a b', 'https://media.example.invalid/a b', 'https://media.example.invalid/a b', 'https://media.example.invalid/a b'),
    ('ftp://files.example.invalid/x', 'ftp://files.example.invalid/x', 'ftp://files.example.invalid/x', 'ftp://files.example.invalid/x'),
    ('example.com/no-scheme', 'example.com/no-scheme', 'example.com/no-scheme', 'example.com/no-scheme'),
    ('', '/', '/', '/'),
    ('not a url', 'not a url', 'not a url', 'not a url'),
    ('https://[::1]:8080/x', 'https://[::1]:8080/x', 'https://[::1]:8080/x', 'https://[::1]:8080/x'),
    # (S0 review fix) the bare youtube.com and bilibili.com hosts, the '#!' and '#…=…' hash routes,
    # youtube-nocookie embeds and a short link with a list
    ('https://youtube.com/watch?v=dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ'),
    ('https://bilibili.com/video/BV1xx411c7mD', 'bili:BV1xx411c7mD:1', 'bili:BV1xx411c7mD:1', 'bili:BV1xx411c7mD:1'),
    ('https://example.com/#!/video/2', 'https://example.com/', 'https://example.com/#!/video/2', 'https://example.com/#!/video/2'),
    ('https://example.com/#!', 'https://example.com/', 'https://example.com/#!', 'https://example.com/#!'),
    ('https://example.com/play#id=3', 'https://example.com/play', 'https://example.com/play#id=3', 'https://example.com/play#id=3'),
    ('https://example.com/play#section', 'https://example.com/play', 'https://example.com/play', 'https://example.com/play'),
    ('https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ', 'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ', 'youtube:dQw4w9WgXcQ'),
    ('https://youtu.be/dQw4w9WgXcQ?list=PLabc', 'https://youtu.be/dQw4w9WgXcQ?list=PLabc', 'youtube:dQw4w9WgXcQ', 'youtube:list:PLabc'),
)
# urlsplit refuses a broken bracketed host: identity raises, link_key keeps the text.
BROKEN = ('https://[::1/x', 'http://[bad')


class IdentityVectorTests(unittest.TestCase):
    def test_identity(self):
        for url, identity, _single, _playlist in VECTORS:
            with self.subTest(url=url):
                self.assertEqual(urls.identity(url), identity)

    def test_link_key(self):
        for url, _identity, single, playlist in VECTORS:
            with self.subTest(url=url):
                self.assertEqual(urls.link_key(url, False), single)
                self.assertEqual(urls.link_key(url, True), playlist)
                self.assertEqual(urls.link_key(url), single, 'playlist is off by default')

    def test_broken_hosts(self):
        for url in BROKEN:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    urls.identity(url)
                self.assertEqual(urls.link_key(url, False), url)
                self.assertEqual(urls.link_key(url, True), url)

    def test_the_library_uses_the_same_function(self):
        import library
        self.assertIs(library.identity, urls.identity)

    def test_the_vectors_cover_each_rule(self):
        answers = {identity for _url, identity, _single, _playlist in VECTORS}
        for prefix in ('youtube:', 'bili:', 'douyin:', 'https://', 'http://', 'ftp://'):
            self.assertTrue(any(answer.startswith(prefix) for answer in answers), prefix)
        self.assertTrue(any(single.startswith('youtube:list:') for _url, _identity, single, _playlist in VECTORS))
        self.assertTrue(any('#' in single for _url, _identity, single, _playlist in VECTORS))
        self.assertGreaterEqual(len(VECTORS), 40)


if __name__ == '__main__':
    unittest.main()
