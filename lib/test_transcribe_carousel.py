"""Carousel failures must not trigger another extraction request."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import transcribe_carousel as carousel


class CarouselTests(unittest.TestCase):
    def run_case(self, create_files):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def download(*args, **kwargs):
                create_files(root / 'AbC' / 'slides')
            with patch.object(carousel, 'downloads_dir', return_value=root), \
                 patch.object(carousel, 'require_ig_cookies', return_value=root/'cookies'), \
                 patch.object(carousel, 'ytdlp', side_effect=download) as fetch, \
                 patch.object(carousel, 'ocr_to_file'):
                code = carousel.main(['https://www.instagram.com/p/AbC/'])
                self.assertEqual(fetch.call_count, 1)
                return code, (root/'AbC.description.txt').read_text()

    def test_empty_media_does_not_retry(self):
        self.assertEqual(self.run_case(lambda p: None)[0], 1)

    def test_description_without_media_is_failure(self):
        self.assertEqual(self.run_case(lambda p: (p/'slide_01.description').write_text('caption'))[0], 1)

    def test_image_only_is_success_and_retains_caption(self):
        def files(p):
            (p/'slide_01.jpg').write_bytes(b'image')
            (p/'slide_01.description').write_text('saved caption')
        self.assertEqual(self.run_case(files), (0, 'saved caption'))


if __name__ == '__main__':
    unittest.main()
