# quick
"""urls.py (1.6.0 WP2 step B): lenient link extraction, YouTube clean-up, link keys, and
the unchanged identity() of 1.5.1.  Pure functions; nothing is requested from any site."""
import _sandbox
import unittest

import engine
import library
import urls
from _sandbox import zh_t

WATCH = 'https://www.youtube.com/watch?v=AAA'


class ExtractTests(unittest.TestCase):
    def test_share_text_yields_the_two_links(self):
        text = '《白羊》这首歌不错 https://youtu.be/AAA?si=abc，快来听！ (https://www.youtube.com/watch?v=BBB).'
        self.assertEqual(urls.extract_urls(text), ['https://youtu.be/AAA?si=abc', 'https://www.youtube.com/watch?v=BBB'])
        cleaned = urls.normalize(urls.extract_urls(text))
        self.assertEqual(cleaned.urls, ['https://youtu.be/AAA', 'https://www.youtube.com/watch?v=BBB'])

    def test_two_links_on_one_line_and_duplicates(self):
        self.assertEqual(urls.extract_urls('https://a.test/1 https://b.test/2\nhttps://a.test/1'),
                         ['https://a.test/1', 'https://b.test/2'])
        self.assertEqual(urls.extract_urls('看这个https://a.test/1。还有HTTPS://B.test/2！'),
                         ['https://a.test/1', 'HTTPS://B.test/2'])

    def test_links_without_a_scheme(self):
        self.assertEqual(urls.extract_urls('www.youtube.com/watch?v=AAA'), ['https://www.youtube.com/watch?v=AAA'])
        self.assertEqual(urls.extract_urls('听听 youtu.be/AAA?si=x 和 music.youtube.com/watch?v=BBB'),
                         ['https://youtu.be/AAA?si=x', 'https://music.youtube.com/watch?v=BBB'])
        # Inside a full link, or glued to a word, these prefixes do not start a second link.
        self.assertEqual(urls.extract_urls('https://www.youtube.com/watch?v=AAA'), ['https://www.youtube.com/watch?v=AAA'])
        self.assertEqual(urls.extract_urls('https://example.org/go?to=www.youtube.com/watch'),
                         ['https://example.org/go?to=www.youtube.com/watch'])
        self.assertEqual(urls.extract_urls('notwww.example.org/x'), [])
        self.assertEqual(urls.extract_urls('example.org/video'), [], 'a bare host without www. is not guessed')

    def test_comment_lines_and_trailing_punctuation(self):
        text = '# https://a.test/comment\n  ; https://a.test/semi\n] https://a.test/bracket\nhttps://a.test/kept'
        self.assertEqual(urls.extract_urls(text), ['https://a.test/kept'])
        for tail in ('.', ',', ';', ':', '!', '?', ')', ']', '}', '>', "'", '"', ').', '?!'):
            self.assertEqual(urls.extract_urls(f'(see https://a.test/x{tail}'), ['https://a.test/x'], tail)
        self.assertEqual(urls.extract_urls('<https://a.test/x> "https://a.test/y"'), ['https://a.test/x', 'https://a.test/y'])

    def test_things_that_are_not_links(self):
        for text in ('', '   ', 'no link here', 'ftp://a.test/x', 'http://', 'https:// a.test', '--exec cmd.exe',
                     'http://[broken', 'mailto:someone@example.org'):
            self.assertEqual(urls.extract_urls(text), [], text)

    def test_parse_download_text(self):
        self.assertEqual(urls.parse_download_text('\n# note\nhttps://a.test/1\n\n歌 https://a.test/2 好听\nhttps://a.test/1\n'),
                         ['https://a.test/1', 'https://a.test/2'])
        with self.assertRaises(ValueError) as caught:
            urls.parse_download_text('https://a.test/1\n\n只是一句话\nhttps://a.test/2')
        self.assertEqual(str(caught.exception), zh_t('core.links.no_link_on_line', line=3))
        for empty in ('', '\n\n', '# only a comment\n; another'):
            with self.assertRaises(ValueError) as caught:
                urls.parse_download_text(empty)
            self.assertEqual(str(caught.exception), zh_t('core.links.none'))

    def test_the_strict_parser_of_1_5_1_is_unchanged(self):
        url = 'https://example.com/watch?v=abc&list=xyz'
        self.assertEqual(engine.parse_urls(f'{url}\n\nhttps://example.org/video\n{url}'), [url, 'https://example.org/video'])
        with self.assertRaisesRegex(ValueError, '第 2 行不是有效的 HTTP / HTTPS 链接'):
            engine.parse_urls('https://example.com/video\n歌 https://a.test/2')


class YouTubeTests(unittest.TestCase):
    def test_normative_examples(self):
        mix = WATCH + '&list=RDMMAAA&start_radio=1'
        self.assertEqual(urls.clean_url(mix), WATCH)
        self.assertEqual(urls.link_key(mix), 'youtube:AAA')
        self.assertEqual(urls.clean_url('https://youtu.be/AAA?si=x'), 'https://youtu.be/AAA')
        self.assertEqual(urls.link_key('https://youtu.be/AAA?si=x'), 'youtube:AAA')
        listed = WATCH + '&list=PLx'
        self.assertEqual(urls.clean_url(listed, playlist=True), listed)
        self.assertIs(urls.clean_url(listed, playlist=True), listed, 'nothing removed: the input itself comes back')
        self.assertEqual(urls.link_key(listed, playlist=True), 'youtube:list:PLx')
        self.assertEqual(urls.clean_url(listed), WATCH)
        self.assertEqual(urls.clean_url(listed, playlist=False), WATCH)
        self.assertEqual(urls.link_key(listed), 'youtube:AAA')
        # A mix is never a playlist to download, also with the playlist option on.
        self.assertEqual(urls.clean_url(mix, playlist=True), WATCH)
        self.assertEqual(urls.link_key(mix, playlist=True), 'youtube:AAA')

    def test_four_spellings_of_one_video_give_one_key(self):
        spellings = [WATCH, WATCH + '&list=RDMMAAA&start_radio=1', 'https://youtu.be/AAA?si=abc',
                     'https://music.youtube.com/watch?v=AAA', 'https://m.youtube.com/watch?v=AAA&feature=share',
                     'https://www.youtube.com/shorts/AAA', 'https://www.youtube.com/live/AAA?si=x',
                     'https://www.youtube.com/embed/AAA', 'https://www.youtube-nocookie.com/embed/AAA',
                     'https://youtube.com/watch?v=AAA&pp=xyz&index=3&ab_channel=someone', 'HTTPS://WWW.YOUTUBE.COM/watch?v=AAA']
        for playlist in (False, True):
            self.assertEqual({urls.link_key(url, playlist) for url in spellings}, {'youtube:AAA'})
            self.assertEqual({urls.link_key(urls.clean_url(url, playlist), playlist) for url in spellings}, {'youtube:AAA'})
        result = urls.normalize(spellings)
        self.assertEqual(result.urls, [WATCH])
        self.assertEqual(result.duplicates, len(spellings) - 1)

    def test_the_host_and_the_other_parameters_are_kept(self):
        self.assertEqual(urls.clean_url('https://music.youtube.com/watch?v=AAA&si=x'), 'https://music.youtube.com/watch?v=AAA')
        self.assertEqual(urls.clean_url('https://music.youtube.com/watch?v=AAA&list=RDAMVMAAA'),
                         'https://music.youtube.com/watch?v=AAA')
        self.assertEqual(urls.clean_url('https://www.youtube.com/watch?t=42&v=AAA&si=x&hl=zh'),
                         'https://www.youtube.com/watch?t=42&v=AAA&hl=zh')
        self.assertEqual(urls.clean_url('https://youtu.be/AAA?t=1m5s&si=x#frag'), 'https://youtu.be/AAA?t=1m5s#frag')
        self.assertEqual(urls.clean_url('https://www.youtube.com/watch?v=AAA&list=PLx&index=3', playlist=True),
                         'https://www.youtube.com/watch?v=AAA&list=PLx')

    def test_links_that_stay_byte_identical(self):
        same = ['https://www.youtube.com/playlist?list=PLx', 'https://www.youtube.com/results?search_query=x',
                'https://example.com/watch?v=abc&list=xyz&si=1&index=2', 'https://a.test/v.mp4?token=1&list=RDx&si=2',
                'https://www.bilibili.com/video/BV123?p=2&si=x', WATCH, 'https://youtu.be/AAA',
                'https://www.youtube.com/@name/videos', 'https://www.youtube.com/watch?v=AAA&t=10',
                'https://www.youtube.com/watch?v=%E4%B8%AD&t=1,2', 'not a link', '']
        for url in same:
            for playlist in (False, True):
                self.assertIs(urls.clean_url(url, playlist), url, url)
        # A list on a link without a video id is the link itself, with or without the playlist option.
        self.assertEqual(urls.link_key('https://www.youtube.com/playlist?list=PLx'), 'youtube:list:PLx')
        self.assertEqual(urls.link_key('https://www.youtube.com/playlist?list=PLx', True), 'youtube:list:PLx')

    def test_video_id(self):
        table = {WATCH: 'AAA', 'https://youtu.be/a-B_9': 'a-B_9', 'https://www.youtube.com/shorts/S1?feature=share': 'S1',
                 'https://www.youtube.com/live/L1': 'L1', 'https://www.youtube.com/embed/E1': 'E1',
                 'https://music.youtube.com/watch?v=M1&list=RDAMVMM1': 'M1', 'https://www.youtube.com/watch/?v=W1': 'W1',
                 'https://www.youtube.com/watch': None, 'https://www.youtube.com/watch?v=': None,
                 'https://www.youtube.com/watch?v=a b': None, 'https://youtu.be/': None,
                 'https://www.youtube.com/playlist?list=PLx&v=AAA': None, 'https://www.youtube.com/embed/videoseries?list=PLx': None,
                 'https://www.youtube.com/results?search_query=x': None, 'https://example.com/watch?v=AAA': None,
                 'https://vimeo.com/123': None, 'nonsense': None, 'http://[broken': None}
        for url, expected in table.items():
            self.assertEqual(urls.video_id(url), expected, url)
        self.assertTrue(urls.is_youtube('https://MUSIC.youtube.com/watch?v=AAA'))
        self.assertTrue(urls.is_youtube('https://www.youtube-nocookie.com/embed/AAA'))
        self.assertTrue(urls.is_youtube('https://www.youtube.com/results?search_query=x'))
        for other in ('https://example.com/watch?v=AAA', 'https://notyoutube.com/', 'https://youtube.com.evil.test/watch?v=AAA',
                      'https://example.invalid/a', '', 'http://[broken'):
            self.assertFalse(urls.is_youtube(other), other)

    def test_classify_and_multi(self):
        kinds = {WATCH: 'video', 'https://youtu.be/AAA?si=x': 'video', WATCH + '&list=RDMMAAA&start_radio=1': 'mix',
                 WATCH + '&list=PLx': 'video_in_list', 'https://www.youtube.com/playlist?list=PLx': 'playlist',
                 'https://www.youtube.com/results?search_query=x': 'search', 'https://www.youtube.com/@name': 'channel',
                 'https://www.youtube.com/@name/videos': 'channel', 'https://www.youtube.com/channel/UC1': 'channel',
                 'https://www.youtube.com/c/name': 'channel', 'https://www.youtube.com/user/name': 'channel',
                 'https://www.youtube.com/': 'other', 'https://www.youtube.com/feed/trending': 'other',
                 'https://example.com/watch?v=abc&list=xyz': 'other', 'https://www.bilibili.com/video/BV123': 'other',
                 'nonsense': 'other'}
        for url, expected in kinds.items():
            self.assertEqual(urls.classify(url), expected, url)
        # multi(): the playlist flag, or a link that is a list by itself.
        for url, kind in kinds.items():
            self.assertTrue(urls.multi(url, playlist=True), url)
            self.assertEqual(urls.multi(url), kind in ('playlist', 'search', 'channel'), url)
            self.assertEqual(urls.multi(url, False), kind in ('playlist', 'search', 'channel'), url)
        self.assertFalse(urls.multi(WATCH))
        self.assertIs(urls.multi(WATCH, playlist=1), True)

    def test_link_keys_of_other_links(self):
        self.assertEqual(urls.link_key('HTTPS://Example.COM/Path?Q=1#frag'), 'https://example.com/Path?Q=1')
        self.assertEqual(urls.link_key('https://example.com'), 'https://example.com/')
        self.assertNotEqual(urls.link_key('https://a.test/v.mp4?token=1'), urls.link_key('https://a.test/v.mp4?token=2'))
        self.assertEqual(urls.link_key('https://www.bilibili.com/video/BV123?p=2&spm=x'), 'bili:BV123:2')
        self.assertEqual(urls.link_key('https://www.douyin.com/video/123456'), 'douyin:123456')
        # YouTube links without a video id: the link itself, without tracking parameters.
        self.assertEqual(urls.link_key('https://www.youtube.com/results?search_query=x&si=1'),
                         urls.link_key('https://WWW.youtube.com/results?search_query=x'))
        self.assertEqual(urls.link_key('https://www.youtube.com/@name'), 'https://www.youtube.com/@name')
        self.assertEqual(urls.link_key('nonsense'), 'nonsense')
        self.assertIsInstance(urls.link_key('http://[broken'), str)

    def test_normalize(self):
        mix = WATCH + '&list=RDMMAAA&start_radio=1'
        listed = 'https://www.youtube.com/watch?v=BBB&list=PLx'
        other = 'https://example.com/watch?v=abc&list=xyz'
        result = urls.normalize([mix, WATCH, listed, 'https://youtu.be/BBB', other, 'https://www.youtube.com/playlist?list=PLx'])
        self.assertEqual(result.urls, [WATCH, 'https://www.youtube.com/watch?v=BBB', other,
                                       'https://www.youtube.com/playlist?list=PLx'])
        self.assertEqual(result.origin, {WATCH: mix, 'https://www.youtube.com/watch?v=BBB': listed, other: other,
                                         'https://www.youtube.com/playlist?list=PLx': 'https://www.youtube.com/playlist?list=PLx'})
        self.assertEqual((result.dropped_lists, result.duplicates), (2, 2))
        self.assertEqual(result.kinds, {'mix': 1, 'video_in_list': 1, 'other': 1, 'playlist': 1})
        # With the playlist option the real list is kept and is its own download; the mix still is not.
        result = urls.normalize([mix, listed, 'https://www.youtube.com/watch?v=CCC&list=PLx', 'https://youtu.be/BBB'], playlist=True)
        self.assertEqual(result.urls, [WATCH, listed, 'https://youtu.be/BBB'])
        self.assertEqual((result.dropped_lists, result.duplicates), (1, 1))
        self.assertEqual(result.kinds, {'mix': 1, 'video_in_list': 1, 'video': 1})
        empty = urls.normalize([])
        self.assertEqual((empty.urls, empty.origin, empty.dropped_lists, empty.duplicates, empty.kinds), ([], {}, 0, 0, {}))

    def test_display(self):
        self.assertEqual(urls.display('https://www.youtube.com/watch?v=AAA'), 'youtube.com/watch?v=AAA')
        self.assertEqual(urls.display('HTTP://WWW.Example.org/x'), 'Example.org/x')
        self.assertEqual(urls.display('https://music.youtube.com/watch?v=AAA'), 'music.youtube.com/watch?v=AAA')
        long = 'https://example.org/' + 'a' * 100
        self.assertEqual(len(urls.display(long)), 60)
        self.assertTrue(urls.display(long).endswith('…'))
        self.assertEqual(urls.display(long, limit=20), 'example.org/aaaaaaa…')
        self.assertEqual(urls.display('youtu.be/AAA'), 'youtu.be/AAA')


class FixRoundOneTests(unittest.TestCase):
    """WP2 fix round 1: hash-routed links (bughunt-1) and glued links (bughunt-2)."""

    def test_a_hash_route_is_part_of_the_key(self):
        song1, song2 = 'https://music.163.com/#/song?id=186016', 'https://music.163.com/#/song?id=347230'
        self.assertEqual(urls.link_key(song1), 'https://music.163.com/#/song?id=186016')
        self.assertNotEqual(urls.link_key(song1), urls.link_key(song2))
        result = urls.normalize([song1, song2, song1])
        self.assertEqual((result.urls, result.duplicates), ([song1, song2], 1))
        for first, second in (('https://example.com/#!/video/1', 'https://example.com/#!/video/2'),
                              ('https://example.com/play#id=1', 'https://example.com/play#id=2'),
                              ('https://example.com/app#view?v=1', 'https://example.com/app#view?v=2')):
            self.assertNotEqual(urls.link_key(first), urls.link_key(second), first)
        # A plain anchor is a place on the page, not another page.
        self.assertEqual(urls.link_key('https://example.com/video/1#comments'), urls.link_key('https://example.com/video/1'))
        self.assertEqual(urls.link_key('https://example.com/video/1#comments'), 'https://example.com/video/1')
        # YouTube: a time stamp is the same video; bilibili and douyin keys name the media id.
        self.assertEqual(urls.link_key('https://www.youtube.com/watch?v=AAA#t=30'), 'youtube:AAA')
        self.assertEqual(urls.link_key('https://www.youtube.com/@name#/x?y=1'), 'https://www.youtube.com/@name')
        self.assertEqual(urls.link_key('https://www.bilibili.com/video/BV123?p=2#/reply?id=1'), 'bili:BV123:2')
        # clean_url never changes another site's link; identity() (stored values) is untouched.
        for link in (song1, 'https://example.com/video/1#comments'):
            self.assertIs(urls.clean_url(link), link)
        self.assertEqual(urls.identity(song1), 'https://music.163.com/')

    def test_links_glued_together_are_split(self):
        first, second = 'https://youtu.be/AAAAAAAAAAA', 'https://youtu.be/BBBBBBBBBBB'
        for separator in (',', ';', '|', '，', '；', '、', ', ', ' | ', ''):
            self.assertEqual(urls.extract_urls(first + separator + second), [first, second], repr(separator))
        self.assertEqual(urls.extract_urls('youtu.be/AAAAAAAAAAA,youtu.be/BBBBBBBBBBB'), [first, second])
        self.assertEqual(urls.extract_urls('https://a.test/1https://b.test/2'), ['https://a.test/1', 'https://b.test/2'])
        self.assertEqual(urls.extract_urls('https://music.163.com/#/song?id=1,https://music.163.com/#/song?id=2'),
                         ['https://music.163.com/#/song?id=1', 'https://music.163.com/#/song?id=2'])
        # Separators inside one link, and a link inside another link's path or query, stay.
        for link in ('https://a.test/x?ids=1,2;3|4', 'https://web.archive.org/web/2020/https://example.com/x',
                     'https://a.test/go?url=https://b.test/y', 'https://a.test/go?u=1&next=https://b.test/y',
                     'https://a.test/x;jsessionid=1'):
            self.assertEqual(urls.extract_urls(link), [link], link)

    def test_trailing_punctuation_and_words_after_a_link(self):
        link = 'https://youtu.be/AAAAAAAAAAA'
        for text in (f'“{link}”', f'‘{link}’', f'{link}：', f'{link}（官方MV）', f'{link}…', f'{link}～', f'{link}·',
                     f'{link}​', f'「{link}」', f'【{link}】', f'{link}。', f'{link}！', f'({link}).', f'{link}》',
                     f'看这个{link}太好听了', f'{link}太好听了！'):
            self.assertEqual(urls.extract_urls(text), [link], text)
            self.assertEqual(urls.link_key(urls.extract_urls(text)[0]), 'youtube:AAAAAAAAAAA', text)
        self.assertEqual(urls.extract_urls('https://www.youtube.com/watch?v=AAAAAAAAAAA&t=5好听'),
                         ['https://www.youtube.com/watch?v=AAAAAAAAAAA&t=5'])
        # Words in another script belong to a channel handle, a search or another site's path.
        for kept in ('https://www.youtube.com/@周杰倫', 'https://www.youtube.com/results?search_query=周杰倫',
                     'https://zh.wikipedia.org/wiki/周杰倫'):
            self.assertEqual(urls.extract_urls(kept), [kept], kept)
        # A video id is ASCII.
        self.assertIsNone(urls.video_id('https://youtu.be/AAA太好听了'))
        self.assertEqual(urls.video_id('https://youtu.be/a-B_9'), 'a-B_9')


class IdentityTests(unittest.TestCase):
    def test_identity_is_the_function_of_1_5_1(self):
        """Its results are stored (subscription entries, archive folders): they never change.
        (2.0.0 S2) The answers below are those of identity() in the 1.5.1 source, frozen as literals
        when the frozen released sources were removed; test_identity_vectors pins many more."""
        self.assertIs(library.identity, urls.identity)
        answers = [
            # the inputs of test_library.py
            ('https://youtu.be/abc', 'youtube:abc'),
            ('https://www.youtube.com/watch?v=abc&utm_source=test', 'youtube:abc'),
            ('https://a.test/v.mp4?token=1', 'https://a.test/v.mp4?token=1'),
            ('https://a.test/v.mp4?token=2', 'https://a.test/v.mp4?token=2'),
            ('https://www.bilibili.com/video/BV123?p=1', 'bili:BV123:1'),
            ('https://www.bilibili.com/video/BV123?p=2', 'bili:BV123:2'),
            ('http://127.0.0.1:8123/new.mp4', 'http://127.0.0.1:8123/new.mp4'),
            ('http://127.0.0.1:8123/fixture.mp4', 'http://127.0.0.1:8123/fixture.mp4'),
            ('http://127.0.0.1:8123/one', 'http://127.0.0.1:8123/one'),
            # and the shapes the new code treats differently in link_key
            ('https://www.youtube.com/watch?v=abc&list=RDMMabc&start_radio=1', 'https://www.youtube.com/watch?v=abc&list=RDMMabc&start_radio=1'),
            ('https://music.youtube.com/watch?v=abc', 'https://music.youtube.com/watch?v=abc'),
            ('https://www.youtube.com/shorts/abc', 'youtube:abc'),
            ('https://www.youtube.com/live/abc?si=x', 'youtube:abc'),
            ('https://m.youtube.com/watch?v=abc', 'youtube:abc'),
            ('https://www.youtube.com/embed/abc', 'https://www.youtube.com/embed/abc'),
            ('https://www.youtube.com/playlist?list=PLx', 'https://www.youtube.com/playlist?list=PLx'),
            ('https://www.youtube.com/results?search_query=x', 'https://www.youtube.com/results?search_query=x'),
            ('https://www.douyin.com/video/123', 'douyin:123'),
            ('https://v.douyin.com/note/9', 'douyin:9'),
            ('https://www.bilibili.com/video/av170001', 'bili:av170001:1'),
            ('HTTPS://Example.COM/Path?Q=1#frag', 'https://example.com/Path?Q=1'),
            ('https://example.com', 'https://example.com/'),
        ]
        for url, answer in answers:
            self.assertEqual(urls.identity(url), answer, url)
        self.assertEqual(urls.identity('https://youtu.be/abc'), 'youtube:abc')
        self.assertEqual(urls.identity('https://youtu.be/abc'), urls.identity('https://www.youtube.com/watch?v=abc&utm_source=test'))
        self.assertNotEqual(urls.identity('https://a.test/v.mp4?token=1'), urls.identity('https://a.test/v.mp4?token=2'))
        self.assertNotEqual(urls.identity('https://www.bilibili.com/video/BV123?p=1'),
                            urls.identity('https://www.bilibili.com/video/BV123?p=2'))
        # identity() itself still gives up on a list and does not know music.youtube.com (1.5.1 behaviour).
        self.assertEqual(urls.identity('https://www.youtube.com/watch?v=abc&list=RDMMabc'),
                         'https://www.youtube.com/watch?v=abc&list=RDMMabc')
        self.assertEqual(urls.identity('https://music.youtube.com/watch?v=abc'), 'https://music.youtube.com/watch?v=abc')


if __name__ == '__main__':
    unittest.main()
