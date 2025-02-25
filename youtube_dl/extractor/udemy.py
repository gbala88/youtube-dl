from __future__ import unicode_literals

import re

from .common import InfoExtractor
from ..compat import (
    compat_HTTPError,
    compat_urllib_request,
    compat_urlparse,
)
from ..utils import (
    determine_ext,
    extract_attributes,
    ExtractorError,
    float_or_none,
    int_or_none,
    sanitized_Request,
    unescapeHTML,
    urlencode_postdata,
)


class UdemyIE(InfoExtractor):
    IE_NAME = 'udemy'
    _VALID_URL = r'''(?x)
                    https?://
                        www\.udemy\.com/
                        (?:
                            [^#]+\#/lecture/|
                            lecture/view/?\?lectureId=|
                            [^/]+/learn/v4/t/lecture/
                        )
                        (?P<id>\d+)
                    '''
    _LOGIN_URL = 'https://www.udemy.com/join/login-popup/?displayType=ajax&showSkipButton=1'
    _ORIGIN_URL = 'https://www.udemy.com'
    _NETRC_MACHINE = 'udemy'

    _TESTS = [{
        'url': 'https://www.udemy.com/java-tutorial/#/lecture/172757',
        'md5': '98eda5b657e752cf945d8445e261b5c5',
        'info_dict': {
            'id': '160614',
            'ext': 'mp4',
            'title': 'Introduction and Installation',
            'description': 'md5:c0d51f6f21ef4ec65f091055a5eef876',
            'duration': 579.29,
        },
        'skip': 'Requires udemy account credentials',
    }, {
        # new URL schema
        'url': 'https://www.udemy.com/electric-bass-right-from-the-start/learn/v4/t/lecture/4580906',
        'only_matching': True,
    }]

    def _extract_course_info(self, webpage, video_id):
        course = self._parse_json(
            unescapeHTML(self._search_regex(
                r'ng-init=["\'].*\bcourse=({.+?});', webpage, 'course', default='{}')),
            video_id, fatal=False) or {}
        course_id = course.get('id') or self._search_regex(
            (r'&quot;id&quot;\s*:\s*(\d+)', r'data-course-id=["\'](\d+)'),
            webpage, 'course id')
        return course_id, course.get('title')

    def _enroll_course(self, base_url, webpage, course_id):
        def combine_url(base_url, url):
            return compat_urlparse.urljoin(base_url, url) if not url.startswith('http') else url

        checkout_url = unescapeHTML(self._search_regex(
            r'href=(["\'])(?P<url>(?:https?://(?:www\.)?udemy\.com)?/payment/checkout/.+?)\1',
            webpage, 'checkout url', group='url', default=None))
        if checkout_url:
            raise ExtractorError(
                'Course %s is not free. You have to pay for it before you can download. '
                'Use this URL to confirm purchase: %s'
                % (course_id, combine_url(base_url, checkout_url)),
                expected=True)

        enroll_url = unescapeHTML(self._search_regex(
            r'href=(["\'])(?P<url>(?:https?://(?:www\.)?udemy\.com)?/course/subscribe/.+?)\1',
            webpage, 'enroll url', group='url', default=None))
        if enroll_url:
            webpage = self._download_webpage(
                combine_url(base_url, enroll_url),
                course_id, 'Enrolling in the course',
                headers={'Referer': base_url})
            if '>You have enrolled in' in webpage:
                self.to_screen('%s: Successfully enrolled in the course' % course_id)

    def _download_lecture(self, course_id, lecture_id):
        return self._download_json(
            'https://www.udemy.com/api-2.0/users/me/subscribed-courses/%s/lectures/%s?'
            % (course_id, lecture_id),
            lecture_id, 'Downloading lecture JSON', query={
                'fields[lecture]': 'title,description,view_html,asset',
                'fields[asset]': 'asset_type,stream_url,thumbnail_url,download_urls,data',
            })

    def _handle_error(self, response):
        if not isinstance(response, dict):
            return
        error = response.get('error')
        if error:
            error_str = 'Udemy returned error #%s: %s' % (error.get('code'), error.get('message'))
            error_data = error.get('data')
            if error_data:
                error_str += ' - %s' % error_data.get('formErrors')
            raise ExtractorError(error_str, expected=True)

    def _download_json(self, url_or_request, *args, **kwargs):
        headers = {
            'X-Udemy-Snail-Case': 'true',
            'X-Requested-With': 'XMLHttpRequest',
        }
        for cookie in self._downloader.cookiejar:
            if cookie.name == 'client_id':
                headers['X-Udemy-Client-Id'] = cookie.value
            elif cookie.name == 'access_token':
                headers['X-Udemy-Bearer-Token'] = cookie.value
                headers['X-Udemy-Authorization'] = 'Bearer %s' % cookie.value

        if isinstance(url_or_request, compat_urllib_request.Request):
            for header, value in headers.items():
                url_or_request.add_header(header, value)
        else:
            url_or_request = sanitized_Request(url_or_request, headers=headers)

        response = super(UdemyIE, self)._download_json(url_or_request, *args, **kwargs)
        self._handle_error(response)
        return response

    def _real_initialize(self):
        self._login()

    def _login(self):
        (username, password) = self._get_login_info()
        if username is None:
            return

        login_popup = self._download_webpage(
            self._LOGIN_URL, None, 'Downloading login popup')

        def is_logged(webpage):
            return any(re.search(p, webpage) for p in (
                r'href=["\'](?:https://www\.udemy\.com)?/user/logout/',
                r'>Logout<'))

        # already logged in
        if is_logged(login_popup):
            return

        login_form = self._form_hidden_inputs('login-form', login_popup)

        login_form.update({
            'email': username,
            'password': password,
        })

        response = self._download_webpage(
            self._LOGIN_URL, None, 'Logging in as %s' % username,
            data=urlencode_postdata(login_form),
            headers={
                'Referer': self._ORIGIN_URL,
                'Origin': self._ORIGIN_URL,
            })

        if not is_logged(response):
            error = self._html_search_regex(
                r'(?s)<div[^>]+class="form-errors[^"]*">(.+?)</div>',
                response, 'error message', default=None)
            if error:
                raise ExtractorError('Unable to login: %s' % error, expected=True)
            raise ExtractorError('Unable to log in')

    def _real_extract(self, url):
        lecture_id = self._match_id(url)

        webpage = self._download_webpage(url, lecture_id)

        course_id, _ = self._extract_course_info(webpage, lecture_id)

        try:
            lecture = self._download_lecture(course_id, lecture_id)
        except ExtractorError as e:
            # Error could possibly mean we are not enrolled in the course
            if isinstance(e.cause, compat_HTTPError) and e.cause.code == 403:
                self._enroll_course(url, webpage, course_id)
                lecture = self._download_lecture(course_id, lecture_id)
            else:
                raise

        title = lecture['title']
        description = lecture.get('description')

        asset = lecture['asset']

        asset_type = asset.get('asset_type') or asset.get('assetType')
        if asset_type != 'Video':
            raise ExtractorError(
                'Lecture %s is not a video' % lecture_id, expected=True)

        stream_url = asset.get('stream_url') or asset.get('streamUrl')
        if stream_url:
            youtube_url = self._search_regex(
                r'(https?://www\.youtube\.com/watch\?v=.*)', stream_url, 'youtube URL', default=None)
            if youtube_url:
                return self.url_result(youtube_url, 'Youtube')

        video_id = asset['id']
        thumbnail = asset.get('thumbnail_url') or asset.get('thumbnailUrl')
        duration = float_or_none(asset.get('data', {}).get('duration'))

        formats = []

        def extract_output_format(src):
            return {
                'url': src['url'],
                'format_id': '%sp' % (src.get('height') or format_id),
                'width': int_or_none(src.get('width')),
                'height': int_or_none(src.get('height')),
                'vbr': int_or_none(src.get('video_bitrate_in_kbps')),
                'vcodec': src.get('video_codec'),
                'fps': int_or_none(src.get('frame_rate')),
                'abr': int_or_none(src.get('audio_bitrate_in_kbps')),
                'acodec': src.get('audio_codec'),
                'asr': int_or_none(src.get('audio_sample_rate')),
                'tbr': int_or_none(src.get('total_bitrate_in_kbps')),
                'filesize': int_or_none(src.get('file_size_in_bytes')),
            }

        outputs = asset.get('data', {}).get('outputs')
        if not isinstance(outputs, dict):
            outputs = {}

        def add_output_format_meta(f, key):
            output = outputs.get(key)
            if isinstance(output, dict):
                output_format = extract_output_format(output)
                output_format.update(f)
                return output_format
            return f

        download_urls = asset.get('download_urls')
        if isinstance(download_urls, dict):
            video = download_urls.get('Video')
            if isinstance(video, list):
                for format_ in video:
                    video_url = format_.get('file')
                    if not video_url:
                        continue
                    format_id = format_.get('label')
                    f = {
                        'url': format_['file'],
                        'format_id': '%sp' % format_id,
                        'height': int_or_none(format_id),
                    }
                    if format_id:
                        # Some videos contain additional metadata (e.g.
                        # https://www.udemy.com/ios9-swift/learn/#/lecture/3383208)
                        f = add_output_format_meta(f, format_id)
                    formats.append(f)

        view_html = lecture.get('view_html')
        if view_html:
            view_html_urls = set()
            for source in re.findall(r'<source[^>]+>', view_html):
                attributes = extract_attributes(source)
                src = attributes.get('src')
                if not src:
                    continue
                res = attributes.get('data-res')
                height = int_or_none(res)
                if src in view_html_urls:
                    continue
                view_html_urls.add(src)
                if attributes.get('type') == 'application/x-mpegURL' or determine_ext(src) == 'm3u8':
                    m3u8_formats = self._extract_m3u8_formats(
                        src, video_id, 'mp4', entry_protocol='m3u8_native',
                        m3u8_id='hls', fatal=False)
                    for f in m3u8_formats:
                        m = re.search(r'/hls_(?P<height>\d{3,4})_(?P<tbr>\d{2,})/', f['url'])
                        if m:
                            if not f.get('height'):
                                f['height'] = int(m.group('height'))
                            if not f.get('tbr'):
                                f['tbr'] = int(m.group('tbr'))
                    formats.extend(m3u8_formats)
                else:
                    formats.append(add_output_format_meta({
                        'url': src,
                        'format_id': '%dp' % height if height else None,
                        'height': height,
                    }, res))

        self._sort_formats(formats, field_preference=('height', 'width', 'tbr', 'format_id'))

        return {
            'id': video_id,
            'title': title,
            'description': description,
            'thumbnail': thumbnail,
            'duration': duration,
            'formats': formats
        }


class UdemyCourseIE(UdemyIE):
    IE_NAME = 'udemy:course'
    _VALID_URL = r'https?://(?:www\.)?udemy\.com/(?P<id>[^/?#&]+)'
    _TESTS = []

    @classmethod
    def suitable(cls, url):
        return False if UdemyIE.suitable(url) else super(UdemyCourseIE, cls).suitable(url)

    def _real_extract(self, url):
        course_path = self._match_id(url)

        webpage = self._download_webpage(url, course_path)

        course_id, title = self._extract_course_info(webpage, course_path)

        self._enroll_course(url, webpage, course_id)

        response = self._download_json(
            'https://www.udemy.com/api-2.0/courses/%s/cached-subscriber-curriculum-items' % course_id,
            course_id, 'Downloading course curriculum', query={
                'fields[chapter]': 'title,object_index',
                'fields[lecture]': 'title,asset',
                'page_size': '1000',
            })

        entries = []
        chapter, chapter_number = [None] * 2
        for entry in response['results']:
            clazz = entry.get('_class')
            if clazz == 'lecture':
                asset = entry.get('asset')
                if isinstance(asset, dict):
                    asset_type = asset.get('asset_type') or asset.get('assetType')
                    if asset_type != 'Video':
                        continue
                lecture_id = entry.get('id')
                if lecture_id:
                    entry = {
                        '_type': 'url_transparent',
                        'url': 'https://www.udemy.com/%s/learn/v4/t/lecture/%s' % (course_path, entry['id']),
                        'title': entry.get('title'),
                        'ie_key': UdemyIE.ie_key(),
                    }
                    if chapter_number:
                        entry['chapter_number'] = chapter_number
                    if chapter:
                        entry['chapter'] = chapter
                    entries.append(entry)
            elif clazz == 'chapter':
                chapter_number = entry.get('object_index')
                chapter = entry.get('title')

        return self.playlist_result(entries, course_id, title)
