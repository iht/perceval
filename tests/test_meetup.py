# -*- coding: utf-8 -*-
#
# Copyright (C) 2015-2019 Bitergia
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
# Authors:
#     Santiago Dueñas <sduenas@bitergia.com>
#

import copy
import datetime
import dateutil.tz
import httpretty
import os
import pkg_resources
import time
import unittest
import unittest.mock
import warnings

import requests

pkg_resources.declare_namespace('perceval.backends')

from perceval.backend import BackendCommandArgumentParser
from perceval.errors import RateLimitError, RepositoryError
from perceval.utils import DEFAULT_DATETIME
from perceval.backends.core.meetup import (Meetup,
                                           MeetupCommand,
                                           MeetupClient,
                                           MIN_RATE_LIMIT)
from base import TestCaseBackendArchive


warnings.filterwarnings("ignore")

MEETUP_URL = 'https://api.meetup.com'
MEETUP_GROUP_URL = MEETUP_URL + '/sqlpass-es'
MEETUP_EVENTS_URL = MEETUP_GROUP_URL + '/events'
MEETUP_EVENT_1_URL = MEETUP_EVENTS_URL + '/1'
MEETUP_EVENT_2_URL = MEETUP_EVENTS_URL + '/2'
MEETUP_EVENT_3_URL = MEETUP_EVENTS_URL + '/3'
MEETUP_EVENT_1_COMMENTS_URL = MEETUP_EVENT_1_URL + '/comments'
MEETUP_EVENT_2_COMMENTS_URL = MEETUP_EVENT_2_URL + '/comments'
MEETUP_EVENT_3_COMMENTS_URL = MEETUP_EVENT_3_URL + '/comments'
MEETUP_EVENT_1_RSVPS_URL = MEETUP_EVENT_1_URL + '/rsvps'
MEETUP_EVENT_2_RSVPS_URL = MEETUP_EVENT_2_URL + '/rsvps'
MEETUP_EVENT_3_RSVPS_URL = MEETUP_EVENT_3_URL + '/rsvps'

MEETUP_COMMENTS_URL = [
    MEETUP_EVENT_1_COMMENTS_URL,
    MEETUP_EVENT_2_COMMENTS_URL,
    MEETUP_EVENT_3_COMMENTS_URL
]
MEETUP_RSVPS_URL = [
    MEETUP_EVENT_1_RSVPS_URL,
    MEETUP_EVENT_2_RSVPS_URL,
    MEETUP_EVENT_3_RSVPS_URL
]


def read_file(filename, mode='r'):
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), filename), mode) as f:
        content = f.read()
    return content


def setup_http_server(rate_limit=-1, reset_rate_limit=-1):
    """Setup a mock HTTP server"""

    http_requests = []

    events_bodies = [
        read_file('data/meetup/meetup_events.json', 'rb'),
        read_file('data/meetup/meetup_events_next.json', 'rb')
    ]
    events_range_body = read_file('data/meetup/meetup_events_range.json', 'rb')
    events_empty_body = read_file('data/meetup/meetup_events_empty.json', 'rb')
    event_comments_body = read_file('data/meetup/meetup_comments.json', 'rb')
    event_rsvps_body = read_file('data/meetup/meetup_rsvps.json', 'rb')

    def request_callback(method, uri, headers, too_many_requests=False):
        last_request = httpretty.last_request()

        if uri.startswith(MEETUP_EVENT_1_COMMENTS_URL):
            body = event_comments_body
        elif uri.startswith(MEETUP_EVENT_2_COMMENTS_URL):
            body = event_comments_body
        elif uri.startswith(MEETUP_EVENT_3_COMMENTS_URL):
            body = event_comments_body
        elif uri.startswith(MEETUP_EVENT_1_RSVPS_URL):
            body = event_rsvps_body
        elif uri.startswith(MEETUP_EVENT_2_RSVPS_URL):
            body = event_rsvps_body
        elif uri.startswith(MEETUP_EVENT_3_RSVPS_URL):
            body = event_rsvps_body
        elif uri.startswith(MEETUP_EVENTS_URL):
            params = last_request.querystring
            scroll = params.get('scroll', None)

            if scroll and scroll[0] == 'since:2016-09-25T00:00:00.000Z':
                # Last events and no pagination
                body = events_bodies[-1]
            elif scroll and scroll[0] == 'since:2016-04-08T00:00:00.000Z':
                body = events_range_body
            elif scroll and scroll[0] == 'since:2017-01-01T00:00:00.000Z':
                body = events_empty_body
            else:
                body = events_bodies.pop(0)

                if events_bodies:
                    # Mock the 'Link' header with a fake URL
                    headers['Link'] = '<' + MEETUP_EVENTS_URL + '>; rel="next"'

                if rate_limit != -1:
                    headers['X-RateLimit-Remaining'] = str(rate_limit)
                if reset_rate_limit != -1:
                    headers['X-RateLimit-Reset'] = str(reset_rate_limit)
        else:
            raise

        if rate_limit == -1:
            headers['X-RateLimit-Remaining'] = '10000000'
        if reset_rate_limit == -1:
            headers['X-RateLimit-Reset'] = '0'

        http_requests.append(last_request)

        return (200, headers, body)

    httpretty.register_uri(httpretty.GET,
                           MEETUP_EVENTS_URL,
                           responses=[
                               httpretty.Response(body=request_callback)
                               for _ in range(2)
                           ])
    for url in MEETUP_COMMENTS_URL:
        httpretty.register_uri(httpretty.GET,
                               url,
                               responses=[
                                   httpretty.Response(body=request_callback)
                               ])
    for url in MEETUP_RSVPS_URL:
        httpretty.register_uri(httpretty.GET,
                               url,
                               responses=[
                                   httpretty.Response(body=request_callback)
                               ])

    return http_requests


class MockedMeetupClient(MeetupClient):
    """Mocked meetup client for testing"""

    def __init__(self, token, is_oauth_token, max_items, min_rate_to_sleep, sleep_for_rate):
        super().__init__(token, is_oauth_token=is_oauth_token, max_items=max_items,
                         min_rate_to_sleep=min_rate_to_sleep,
                         sleep_for_rate=sleep_for_rate)
        self.rate_limit_reset_ts = -1


class TestMeetupBackend(unittest.TestCase):
    """Meetup backend tests"""

    def setUp(self):
        warnings.simplefilter("ignore")

    def test_initialization(self):
        """Test whether attributes are initialized"""

        meetup = Meetup('mygroup', 'aaaa', max_items=5, tag='test',
                        sleep_for_rate=True, min_rate_to_sleep=10, sleep_time=60)

        self.assertEqual(meetup.origin, 'https://meetup.com/')
        self.assertEqual(meetup.tag, 'test')
        self.assertEqual(meetup.group, 'mygroup')
        self.assertEqual(meetup.max_items, 5)
        self.assertIsNone(meetup.client)
        self.assertFalse(meetup.is_oauth_token)

        # When tag is empty or None it will be set to
        # the value in URL
        meetup = Meetup('mygroup', 'aaaa')
        self.assertEqual(meetup.origin, 'https://meetup.com/')
        self.assertEqual(meetup.tag, 'https://meetup.com/')
        self.assertFalse(meetup.is_oauth_token)

        meetup = Meetup('mygroup', 'aaaa', tag='')
        self.assertEqual(meetup.origin, 'https://meetup.com/')
        self.assertEqual(meetup.tag, 'https://meetup.com/')
        self.assertFalse(meetup.is_oauth_token)

    def test_initialization_oauth(self):
        """Test whether attributes are initialized with an oauth token"""

        meetup = Meetup('mygroup', 'aaaa', is_oauth_token=True, max_items=5, tag='test',
                        sleep_for_rate=True, min_rate_to_sleep=10, sleep_time=60)

        self.assertEqual(meetup.origin, 'https://meetup.com/')
        self.assertEqual(meetup.tag, 'test')
        self.assertEqual(meetup.group, 'mygroup')
        self.assertEqual(meetup.max_items, 5)
        self.assertIsNone(meetup.client)
        self.assertTrue(meetup.is_oauth_token)

    def test_initialization_warning(self):
        with self.assertWarns(DeprecationWarning):
            _ = Meetup('mygroup', 'aaaa')

    def test_has_archiving(self):
        """Test if it returns True when has_archiving is called"""

        self.assertTrue(Meetup.has_archiving())

    def test_has_resuming(self):
        """Test if it returns True when has_resuming is called"""

        self.assertTrue(Meetup.has_resuming())

    @httpretty.activate
    def test_fetch(self):
        """Test whether it fetches a set of events"""

        http_requests = setup_http_server()

        meetup = Meetup('sqlpass-es', 'aaaa', max_items=2)
        events = [event for event in meetup.fetch(from_date=None)]

        expected = [('1', '0d07fe36f994a6c78dfcf60fb73674bcf158cb5a', 1460065164.0, 2, 3),
                    ('2', '24b47b622eb33965676dd951b18eea7689b1d81c', 1465503498.0, 2, 3),
                    ('3', 'a42b7cf556c17b17f05b951e2eb5e07a7cb0a731', 1474842748.0, 2, 3)]

        self.assertEqual(len(events), len(expected))

        for x in range(len(events)):
            event = events[x]
            expc = expected[x]
            self.assertEqual(event['data']['id'], expc[0])
            self.assertEqual(event['uuid'], expc[1])
            self.assertEqual(event['origin'], 'https://meetup.com/')
            self.assertEqual(event['updated_on'], expc[2])
            self.assertEqual(event['category'], 'event')
            self.assertEqual(event['tag'], 'https://meetup.com/')
            self.assertEqual(event['classified_fields_filtered'], None)
            self.assertIn('topics', event['data']['group'])
            self.assertEqual(len(event['data']['comments']), expc[3])
            self.assertEqual(len(event['data']['rsvps']), expc[4])

        # Check requests
        expected = [
            {
                'fields': ['event_hosts,featured,group_topics,plain_text_description,rsvpable,series'],
                'key': ['aaaa'],
                'order': ['updated'],
                'page': ['2'],
                'scroll': ['since:1970-01-01T00:00:00.000Z'],
                'sign': ['true'],
                'status': ['cancelled,upcoming,past,proposed,suggested']
            },
            {
                'key': ['aaaa'],
                'page': ['2'],
                'sign': ['true']
            },
            {
                'fields': ['attendance_status'],
                'key': ['aaaa'],
                'page': ['2'],
                'response': ['yes,no'],
                'sign': ['true']
            },
            {
                'key': ['aaaa'],
                'page': ['2'],
                'sign': ['true']
            },
            {
                'fields': ['attendance_status'],
                'key': ['aaaa'],
                'page': ['2'],
                'response': ['yes,no'],
                'sign': ['true']
            },
            {
                'key': ['aaaa'],
                'sign': ['true']
            },
            {
                'key': ['aaaa'],
                'page': ['2'],
                'sign': ['true']
            },
            {
                'fields': ['attendance_status'],
                'key': ['aaaa'],
                'page': ['2'],
                'response': ['yes,no'],
                'sign': ['true']
            }
        ]

        self.assertEqual(len(http_requests), len(expected))

        for i in range(len(expected)):
            self.assertDictEqual(http_requests[i].querystring, expected[i])

    @httpretty.activate
    def test_fetch_oauth_token(self):
        """Test whether it fetches a set of events when using an oauth token"""

        http_requests = setup_http_server()

        meetup = Meetup('sqlpass-es', 'aaaa', is_oauth_token=True, max_items=2)
        events = [event for event in meetup.fetch(from_date=None)]

        expected = [('1', '0d07fe36f994a6c78dfcf60fb73674bcf158cb5a', 1460065164.0, 2, 3),
                    ('2', '24b47b622eb33965676dd951b18eea7689b1d81c', 1465503498.0, 2, 3),
                    ('3', 'a42b7cf556c17b17f05b951e2eb5e07a7cb0a731', 1474842748.0, 2, 3)]

        self.assertEqual(len(events), len(expected))

        for x in range(len(events)):
            event = events[x]
            expc = expected[x]
            self.assertEqual(event['data']['id'], expc[0])
            self.assertEqual(event['uuid'], expc[1])
            self.assertEqual(event['origin'], 'https://meetup.com/')
            self.assertEqual(event['updated_on'], expc[2])
            self.assertEqual(event['category'], 'event')
            self.assertEqual(event['tag'], 'https://meetup.com/')
            self.assertEqual(event['classified_fields_filtered'], None)
            self.assertIn('topics', event['data']['group'])
            self.assertEqual(len(event['data']['comments']), expc[3])
            self.assertEqual(len(event['data']['rsvps']), expc[4])

        # Check requests
        expected = [
            {
                'fields': ['event_hosts,featured,group_topics,plain_text_description,rsvpable,series'],
                'access_token': ['aaaa'],
                'order': ['updated'],
                'page': ['2'],
                'scroll': ['since:1970-01-01T00:00:00.000Z'],
                'status': ['cancelled,upcoming,past,proposed,suggested']
            },
            {
                'access_token': ['aaaa'],
                'page': ['2']
            },
            {
                'fields': ['attendance_status'],
                'access_token': ['aaaa'],
                'page': ['2'],
                'response': ['yes,no']
            },
            {
                'access_token': ['aaaa'],
                'page': ['2']
            },
            {
                'fields': ['attendance_status'],
                'access_token': ['aaaa'],
                'page': ['2'],
                'response': ['yes,no']
            },
            {
                'access_token': ['aaaa']
            },
            {
                'access_token': ['aaaa'],
                'page': ['2']
            },
            {
                'fields': ['attendance_status'],
                'access_token': ['aaaa'],
                'page': ['2'],
                'response': ['yes,no']
            }
        ]

        self.assertEqual(len(http_requests), len(expected))

        for i in range(len(expected)):
            self.assertDictEqual(http_requests[i].querystring, expected[i])

    @httpretty.activate
    def test_fetch_from_date(self):
        """Test whether if fetches a set of events from the given date"""

        http_requests = setup_http_server()

        from_date = datetime.datetime(2016, 9, 25)

        meetup = Meetup('sqlpass-es', 'aaaa', max_items=2)
        events = [event for event in meetup.fetch(from_date=from_date)]

        expected = [('3', 'a42b7cf556c17b17f05b951e2eb5e07a7cb0a731', 1474842748.0, 2, 3)]

        self.assertEqual(len(events), len(expected))

        for x in range(len(events)):
            event = events[x]
            expc = expected[x]
            self.assertEqual(event['data']['id'], expc[0])
            self.assertEqual(event['uuid'], expc[1])
            self.assertEqual(event['origin'], 'https://meetup.com/')
            self.assertEqual(event['updated_on'], expc[2])
            self.assertEqual(event['category'], 'event')
            self.assertEqual(event['tag'], 'https://meetup.com/')
            self.assertEqual(event['classified_fields_filtered'], None)
            self.assertEqual(len(event['data']['comments']), expc[3])
            self.assertEqual(len(event['data']['rsvps']), expc[4])

        # Check requests
        expected = [
            {
                'fields': ['event_hosts,featured,group_topics,plain_text_description,rsvpable,series'],
                'key': ['aaaa'],
                'order': ['updated'],
                'page': ['2'],
                'scroll': ['since:2016-09-25T00:00:00.000Z'],
                'sign': ['true'],
                'status': ['cancelled,upcoming,past,proposed,suggested']
            },
            {
                'key': ['aaaa'],
                'page': ['2'],
                'sign': ['true']
            },
            {
                'fields': ['attendance_status'],
                'key': ['aaaa'],
                'page': ['2'],
                'response': ['yes,no'],
                'sign': ['true']
            }
        ]

        self.assertEqual(len(http_requests), len(expected))

        for i in range(len(expected)):
            self.assertDictEqual(http_requests[i].querystring, expected[i])

    @httpretty.activate
    def test_fetch_to_date(self):
        """Test whether if fetches a set of events updated before the given date"""

        http_requests = setup_http_server()

        to_date = datetime.datetime(2016, 9, 25)

        meetup = Meetup('sqlpass-es', 'aaaa', max_items=2)
        events = [event for event in meetup.fetch(to_date=to_date)]

        expected = [('1', '0d07fe36f994a6c78dfcf60fb73674bcf158cb5a', 1460065164.0, 2, 3),
                    ('2', '24b47b622eb33965676dd951b18eea7689b1d81c', 1465503498.0, 2, 3)]

        self.assertEqual(len(events), len(expected))

        for x in range(len(events)):
            event = events[x]
            expc = expected[x]
            self.assertEqual(event['data']['id'], expc[0])
            self.assertEqual(event['uuid'], expc[1])
            self.assertEqual(event['origin'], 'https://meetup.com/')
            self.assertEqual(event['updated_on'], expc[2])
            self.assertEqual(event['category'], 'event')
            self.assertEqual(event['tag'], 'https://meetup.com/')
            self.assertEqual(event['classified_fields_filtered'], None)
            self.assertEqual(len(event['data']['comments']), expc[3])
            self.assertEqual(len(event['data']['rsvps']), expc[4])

        # Check requests
        expected = [
            {
                'fields': ['event_hosts,featured,group_topics,plain_text_description,rsvpable,series'],
                'key': ['aaaa'],
                'order': ['updated'],
                'page': ['2'],
                'scroll': ['since:1970-01-01T00:00:00.000Z'],
                'sign': ['true'],
                'status': ['cancelled,upcoming,past,proposed,suggested']
            },
            {
                'key': ['aaaa'],
                'page': ['2'],
                'sign': ['true']
            },
            {
                'fields': ['attendance_status'],
                'key': ['aaaa'],
                'page': ['2'],
                'response': ['yes,no'],
                'sign': ['true']
            },
            {
                'key': ['aaaa'],
                'page': ['2'],
                'sign': ['true']
            },
            {
                'fields': ['attendance_status'],
                'key': ['aaaa'],
                'page': ['2'],
                'response': ['yes,no'],
                'sign': ['true']
            },
            {
                'key': ['aaaa'],
                'sign': ['true']
            },
            {
                'key': ['aaaa'],
                'page': ['2'],
                'sign': ['true']
            },
            {
                'fields': ['attendance_status'],
                'key': ['aaaa'],
                'page': ['2'],
                'response': ['yes,no'],
                'sign': ['true']
            }
        ]

        self.assertEqual(len(http_requests), len(expected))

        for i in range(len(expected)):
            self.assertDictEqual(http_requests[i].querystring, expected[i])

    @httpretty.activate
    def test_fetch_date_range(self):
        """Test whether if fetches a set of events updated withing the given range"""

        http_requests = setup_http_server()

        from_date = datetime.datetime(2016, 4, 8)
        to_date = datetime.datetime(2016, 9, 25)

        meetup = Meetup('sqlpass-es', 'aaaa', max_items=2)
        events = [event for event in meetup.fetch(from_date=from_date,
                                                  to_date=to_date)]

        self.assertEqual(len(events), 1)

        event = events[0]
        self.assertEqual(event['data']['id'], '2')
        self.assertEqual(event['uuid'], '24b47b622eb33965676dd951b18eea7689b1d81c')
        self.assertEqual(event['origin'], 'https://meetup.com/')
        self.assertEqual(event['updated_on'], 1465503498.0)
        self.assertEqual(event['category'], 'event')
        self.assertEqual(event['tag'], 'https://meetup.com/')
        self.assertEqual(event['classified_fields_filtered'], None)
        self.assertEqual(len(event['data']['comments']), 2)
        self.assertEqual(len(event['data']['rsvps']), 3)

        # Check requests
        expected = [
            {
                'fields': ['event_hosts,featured,group_topics,plain_text_description,rsvpable,series'],
                'key': ['aaaa'],
                'order': ['updated'],
                'page': ['2'],
                'scroll': ['since:2016-04-08T00:00:00.000Z'],
                'sign': ['true'],
                'status': ['cancelled,upcoming,past,proposed,suggested']
            },
            {
                'key': ['aaaa'],
                'page': ['2'],
                'sign': ['true']
            },
            {
                'fields': ['attendance_status'],
                'key': ['aaaa'],
                'page': ['2'],
                'response': ['yes,no'],
                'sign': ['true']
            }
        ]

        self.assertEqual(len(http_requests), len(expected))

        for i in range(len(expected)):
            self.assertDictEqual(http_requests[i].querystring, expected[i])

    @httpretty.activate
    def test_fetch_filtering_classified_fields(self):
        """Test it it removes classified fields from a set of fetched items"""

        http_requests = setup_http_server()

        meetup = Meetup('sqlpass-es', 'aaaa', max_items=2)
        events = [event for event in meetup.fetch(from_date=None, filter_classified=True)]

        expected = [('1', '0d07fe36f994a6c78dfcf60fb73674bcf158cb5a', 1460065164.0, 2, 3),
                    ('2', '24b47b622eb33965676dd951b18eea7689b1d81c', 1465503498.0, 2, 3),
                    ('3', 'a42b7cf556c17b17f05b951e2eb5e07a7cb0a731', 1474842748.0, 2, 3)]

        self.assertEqual(len(events), len(expected))

        for x in range(len(events)):
            event = events[x]
            expc = expected[x]
            self.assertEqual(event['data']['id'], expc[0])
            self.assertEqual(event['uuid'], expc[1])
            self.assertEqual(event['origin'], 'https://meetup.com/')
            self.assertEqual(event['updated_on'], expc[2])
            self.assertEqual(event['category'], 'event')
            self.assertEqual(event['tag'], 'https://meetup.com/')
            self.assertEqual(len(event['data']['comments']), expc[3])

            # Check classified items filtering
            self.assertEqual(event['classified_fields_filtered'],
                             ['group.topics', 'event_hosts', 'rsvps', 'venue'])
            self.assertNotIn('topics', event['data']['group'])
            self.assertNotIn('event_hosts', event['data'])
            self.assertNotIn('rsvps', event['data'])
            self.assertNotIn('venue', event['data'])

        # Check requests
        expected = [
            {
                'fields': ['event_hosts,featured,group_topics,plain_text_description,rsvpable,series'],
                'key': ['aaaa'],
                'order': ['updated'],
                'page': ['2'],
                'scroll': ['since:1970-01-01T00:00:00.000Z'],
                'sign': ['true'],
                'status': ['cancelled,upcoming,past,proposed,suggested']
            },
            {
                'key': ['aaaa'],
                'page': ['2'],
                'sign': ['true']
            },
            {
                'fields': ['attendance_status'],
                'key': ['aaaa'],
                'page': ['2'],
                'response': ['yes,no'],
                'sign': ['true']
            },
            {
                'key': ['aaaa'],
                'page': ['2'],
                'sign': ['true']
            },
            {
                'fields': ['attendance_status'],
                'key': ['aaaa'],
                'page': ['2'],
                'response': ['yes,no'],
                'sign': ['true']
            },
            {
                'key': ['aaaa'],
                'sign': ['true']
            },
            {
                'key': ['aaaa'],
                'page': ['2'],
                'sign': ['true']
            },
            {
                'fields': ['attendance_status'],
                'key': ['aaaa'],
                'page': ['2'],
                'response': ['yes,no'],
                'sign': ['true']
            }
        ]

        self.assertEqual(len(http_requests), len(expected))

        for i in range(len(expected)):
            self.assertDictEqual(http_requests[i].querystring, expected[i])

    @httpretty.activate
    def test_fetch_empty(self):
        """Test if nothing is returned when there are no events"""

        http_requests = setup_http_server()

        from_date = datetime.datetime(2017, 1, 1)

        meetup = Meetup('sqlpass-es', 'aaaa', max_items=2)
        events = [event for event in meetup.fetch(from_date=from_date)]

        self.assertEqual(len(events), 0)

        # Check requests
        expected = {
            'fields': ['event_hosts,featured,group_topics,plain_text_description,rsvpable,series'],
            'key': ['aaaa'],
            'order': ['updated'],
            'page': ['2'],
            'scroll': ['since:2017-01-01T00:00:00.000Z'],
            'sign': ['true'],
            'status': ['cancelled,upcoming,past,proposed,suggested']
        }

        self.assertEqual(len(http_requests), 1)
        self.assertDictEqual(http_requests[0].querystring, expected)

    def test_parse_json(self):
        """Test if it parses a JSON stream"""

        raw_json = read_file('data/meetup/meetup_events.json')

        items = Meetup.parse_json(raw_json)
        results = [item for item in items]

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['id'], '1')
        self.assertEqual(results[1]['id'], '2')

        # Parse a file without results
        raw_json = read_file('data/meetup/meetup_events_empty.json')

        items = Meetup.parse_json(raw_json)
        results = [item for item in items]

        self.assertEqual(len(results), 0)


class TestMeetupBackendArchive(TestCaseBackendArchive):
    """Meetup backend tests using an archive"""

    def setUp(self):
        super().setUp()
        self.backend_write_archive = Meetup('sqlpass-es', 'aaaa', max_items=2, archive=self.archive)
        self.backend_read_archive = Meetup('sqlpass-es', 'bbbb', max_items=2, archive=self.archive)

    @httpretty.activate
    def test_fetch_from_archive(self):
        """Test whether it fetches a set of events from archive"""

        setup_http_server()
        self._test_fetch_from_archive()

    @httpretty.activate
    def test_fetch_from_date_archive(self):
        """Test whether if fetches a set of events from the given date from archive"""

        setup_http_server()

        from_date = datetime.datetime(2016, 9, 25)
        self._test_fetch_from_archive(from_date=from_date)

    @httpretty.activate
    def test_fetch_to_date(self):
        """Test whether if fetches a set of events updated before the given date from archive"""

        setup_http_server()

        to_date = datetime.datetime(2016, 9, 25)
        self._test_fetch_from_archive(to_date=to_date)

    @httpretty.activate
    def test_fetch_date_range_from_archive(self):
        """Test whether if fetches a set of events updated withing the given range from archive"""

        setup_http_server()

        from_date = datetime.datetime(2016, 4, 8)
        to_date = datetime.datetime(2016, 9, 25)

        self._test_fetch_from_archive(from_date=from_date, to_date=to_date)

    @httpretty.activate
    def test_fetch_empty(self):
        """Test if nothing is returned when there are no events in the archive"""

        setup_http_server()

        from_date = datetime.datetime(2017, 1, 1)
        self._test_fetch_from_archive(from_date=from_date)


class TestMeetupCommand(unittest.TestCase):
    """Tests for MeetupCommand class"""

    def test_backend_class(self):
        """Test if the backend class is Meetup"""

        self.assertIs(MeetupCommand.BACKEND, Meetup)

    def test_setup_cmd_parser(self):
        """Test if it parser object is correctly initialized"""

        parser = MeetupCommand.setup_cmd_parser()
        self.assertIsInstance(parser, BackendCommandArgumentParser)
        self.assertEqual(parser._categories, Meetup.CATEGORIES)

        args = ['sqlpass-es',
                '--api-token', 'aaaa',
                '--max-items', '5',
                '--tag', 'test',
                '--no-archive',
                '--from-date', '1970-01-01',
                '--to-date', '2016-01-01',
                '--sleep-for-rate',
                '--min-rate-to-sleep', '10',
                '--sleep-time', '10',
                '--filter-classified']

        expected_ts = datetime.datetime(2016, 1, 1, 0, 0, 0,
                                        tzinfo=dateutil.tz.tzutc())

        parsed_args = parser.parse(*args)
        self.assertEqual(parsed_args.group, 'sqlpass-es')
        self.assertEqual(parsed_args.api_token, 'aaaa')
        self.assertEqual(parsed_args.max_items, 5)
        self.assertEqual(parsed_args.tag, 'test')
        self.assertTrue(parsed_args.no_archive)
        self.assertEqual(parsed_args.from_date, DEFAULT_DATETIME)
        self.assertEqual(parsed_args.to_date, expected_ts)
        self.assertTrue(parsed_args.sleep_for_rate)
        self.assertEqual(parsed_args.min_rate_to_sleep, 10)
        self.assertEqual(parsed_args.sleep_time, 10)
        self.assertTrue(parsed_args.filter_classified)
        self.assertFalse(parsed_args.is_oauth_token)

        args = ['sqlpass-es',
                '--api-token', 'aaaa',
                '--max-items', '5',
                '--tag', 'test',
                '--no-archive',
                '--sleep-for-rate',
                '--min-rate-to-sleep', '10',
                '--sleep-time', '10',
                '--is-oauth-token']

        parsed_args = parser.parse(*args)
        self.assertEqual(parsed_args.group, 'sqlpass-es')
        self.assertEqual(parsed_args.api_token, 'aaaa')
        self.assertEqual(parsed_args.max_items, 5)
        self.assertEqual(parsed_args.tag, 'test')
        self.assertTrue(parsed_args.no_archive)
        self.assertEqual(parsed_args.from_date, DEFAULT_DATETIME)
        self.assertTrue(parsed_args.sleep_for_rate)
        self.assertEqual(parsed_args.min_rate_to_sleep, 10)
        self.assertEqual(parsed_args.sleep_time, 10)
        self.assertTrue(parsed_args.is_oauth_token)


class TestMeetupClient(unittest.TestCase):
    """Meetup REST API client tests.

    These tests not check the body of the response, only if the call
    was well formed and if a response was obtained. Due to this, take
    into account that the body returned on each request might not
    match with the parameters from the request.
    """
    def test_init(self):
        """Test initialization"""

        client = MeetupClient('aaaa', max_items=10)
        self.assertEqual(client.api_key, 'aaaa')
        self.assertEqual(client.max_items, 10)
        self.assertFalse(client.sleep_for_rate)
        self.assertEqual(client.min_rate_to_sleep, MIN_RATE_LIMIT)
        self.assertFalse(client.is_oauth_token)

        client = MeetupClient('aaaa', max_items=10,
                              sleep_for_rate=True,
                              min_rate_to_sleep=4)
        self.assertEqual(client.api_key, 'aaaa')
        self.assertEqual(client.max_items, 10)
        self.assertTrue(client.sleep_for_rate)
        self.assertEqual(client.min_rate_to_sleep, 4)
        self.assertFalse(client.is_oauth_token)

        # Max rate limit is never overtaken
        client = MeetupClient('aaaa', max_items=10,
                              sleep_for_rate=True,
                              min_rate_to_sleep=100000000)
        self.assertEqual(client.min_rate_to_sleep, client.MAX_RATE_LIMIT)
        self.assertFalse(client.is_oauth_token)

    def test_init_oauth_token(self):
        """Test initialization with an oauth token"""

        client = MeetupClient('aaaa', is_oauth_token=True, max_items=10)
        self.assertEqual(client.api_key, 'aaaa')
        self.assertEqual(client.max_items, 10)
        self.assertFalse(client.sleep_for_rate)
        self.assertEqual(client.min_rate_to_sleep, MIN_RATE_LIMIT)
        self.assertTrue(client.is_oauth_token)

    @httpretty.activate
    def test_group_gone(self):
        """Test whether the group gone exception (HTTP 410) is properly handled"""

        httpretty.register_uri(httpretty.GET,
                               MEETUP_EVENTS_URL,
                               body="",
                               status=410)

        client = MeetupClient('aaaa', max_items=2)
        events = client.events('sqlpass-es')

        with self.assertRaises(RepositoryError):
            _ = [event for event in events]

    @httpretty.activate
    def test_events_error(self):
        """Test whether HTTP errors different from 410 are thrown when fetching event pages"""

        httpretty.register_uri(httpretty.GET,
                               MEETUP_EVENTS_URL,
                               body="",
                               status=401)

        client = MeetupClient('aaaa', max_items=2)
        events = client.events('sqlpass-es')

        with self.assertRaises(requests.exceptions.HTTPError):
            _ = [event for event in events]

    @httpretty.activate
    def test_events(self):
        """Test events API call"""

        http_requests = setup_http_server()

        client = MeetupClient('aaaa', max_items=2)

        from_date = datetime.datetime(2016, 1, 1)

        # Call API
        events = client.events('sqlpass-es', from_date=from_date)
        result = [event for event in events]

        self.assertEqual(len(result), 2)

        expected = [
            {
                'fields': ['event_hosts,featured,group_topics,plain_text_description,rsvpable,series'],
                'key': ['aaaa'],
                'order': ['updated'],
                'page': ['2'],
                'scroll': ['since:2016-01-01T00:00:00.000Z'],
                'sign': ['true'],
                'status': ['cancelled,upcoming,past,proposed,suggested']
            },
            {
                'key': ['aaaa'],
                'sign': ['true']
            }
        ]

        self.assertEqual(len(http_requests), 2)

        for x in range(0, len(http_requests)):
            req = http_requests[x]
            self.assertEqual(req.method, 'GET')
            self.assertRegex(req.path, '/sqlpass-es/events')
            self.assertDictEqual(req.querystring, expected[x])

    @httpretty.activate
    def test_events_oauth_token(self):
        """Test events API call with oauth token"""

        http_requests = setup_http_server()

        client = MeetupClient('aaaa', is_oauth_token=True, max_items=2)

        from_date = datetime.datetime(2016, 1, 1)

        # Call API
        events = client.events('sqlpass-es', from_date=from_date)
        result = [event for event in events]

        self.assertEqual(len(result), 2)

        expected = [
            {
                'fields': ['event_hosts,featured,group_topics,plain_text_description,rsvpable,series'],
                'access_token': ['aaaa'],
                'order': ['updated'],
                'page': ['2'],
                'scroll': ['since:2016-01-01T00:00:00.000Z'],
                'status': ['cancelled,upcoming,past,proposed,suggested']
            },
            {
                'access_token': ['aaaa']
            }
        ]

        self.assertEqual(len(http_requests), 2)

        for x in range(0, len(http_requests)):
            req = http_requests[x]
            self.assertEqual(req.method, 'GET')
            self.assertRegex(req.path, '/sqlpass-es/events')
            self.assertDictEqual(req.querystring, expected[x])

    @httpretty.activate
    def test_comments(self):
        """Test comments API call"""

        http_requests = setup_http_server()

        client = MeetupClient('aaaa', max_items=2)

        # Call API
        comments = client.comments('sqlpass-es', '1')
        result = [comment for comment in comments]

        self.assertEqual(len(result), 1)

        expected = {
            'key': ['aaaa'],
            'page': ['2'],
            'sign': ['true']
        }

        self.assertEqual(len(http_requests), 1)

        req = http_requests[0]
        self.assertEqual(req.method, 'GET')
        self.assertRegex(req.path, '/sqlpass-es/events/1/comments')
        self.assertDictEqual(req.querystring, expected)

    @httpretty.activate
    def test_comments_oauth_token(self):
        """Test comments API call with oauth token"""

        http_requests = setup_http_server()

        client = MeetupClient('aaaa', is_oauth_token=True, max_items=2)

        # Call API
        comments = client.comments('sqlpass-es', '1')
        result = [comment for comment in comments]

        self.assertEqual(len(result), 1)

        expected = {
            'access_token': ['aaaa'],
            'page': ['2']
        }

        self.assertEqual(len(http_requests), 1)

        req = http_requests[0]
        self.assertEqual(req.method, 'GET')
        self.assertRegex(req.path, '/sqlpass-es/events/1/comments')
        self.assertDictEqual(req.querystring, expected)

    @httpretty.activate
    def test_rsvps(self):
        """Test rsvps API call"""

        http_requests = setup_http_server()

        client = MeetupClient('aaaa', max_items=2)

        # Call API
        rsvps = client.rsvps('sqlpass-es', '1')
        result = [rsvp for rsvp in rsvps]

        self.assertEqual(len(result), 1)

        expected = {
            'fields': ['attendance_status'],
            'key': ['aaaa'],
            'page': ['2'],
            'response': ['yes,no'],
            'sign': ['true']
        }

        self.assertEqual(len(http_requests), 1)

        req = http_requests[0]
        self.assertEqual(req.method, 'GET')
        self.assertRegex(req.path, '/sqlpass-es/events/1/rsvps')
        self.assertDictEqual(req.querystring, expected)

    @httpretty.activate
    def test_rsvps_oauth_token(self):
        """Test rsvps API call with oauth token"""

        http_requests = setup_http_server()

        client = MeetupClient('aaaa', is_oauth_token=True, max_items=2)

        # Call API
        rsvps = client.rsvps('sqlpass-es', '1')
        result = [rsvp for rsvp in rsvps]

        self.assertEqual(len(result), 1)

        expected = {
            'fields': ['attendance_status'],
            'access_token': ['aaaa'],
            'page': ['2'],
            'response': ['yes,no']
        }

        self.assertEqual(len(http_requests), 1)

        req = http_requests[0]
        self.assertEqual(req.method, 'GET')
        self.assertRegex(req.path, '/sqlpass-es/events/1/rsvps')
        self.assertDictEqual(req.querystring, expected)

    def test_calculate_time_to_reset(self):
        """Test whether the time to reset is zero if the sleep time is negative"""

        client = MockedMeetupClient('aaaa', is_oauth_token=False,
                                    max_items=2,
                                    min_rate_to_sleep=2,
                                    sleep_for_rate=True)

        time_to_reset = client.calculate_time_to_reset()
        self.assertEqual(time_to_reset, 0)

    def test_calculate_time_to_reset_oauth_token(self):
        """Test whether the time to reset is zero if the sleep time is negative"""

        # Rate limit headers don't differ when using oauth tokens and api keys
        client = MockedMeetupClient('aaaa', is_oauth_token=True,
                                    max_items=2,
                                    min_rate_to_sleep=2,
                                    sleep_for_rate=True)

        time_to_reset = client.calculate_time_to_reset()
        self.assertEqual(time_to_reset, 0)

    @httpretty.activate
    def test_sleep_for_rate(self):
        """ Test if the clients sleeps when the rate limit is reached"""

        wait_to_reset = 1

        http_requests = setup_http_server(rate_limit=0,
                                          reset_rate_limit=wait_to_reset)

        client = MeetupClient('aaaa', max_items=2,
                              min_rate_to_sleep=2,
                              sleep_for_rate=True)

        # Call API
        before = float(time.time())
        events = client.events('sqlpass-es')
        results = [event for event in events]
        after = float(time.time())
        diff = after - before

        self.assertGreaterEqual(diff, wait_to_reset)
        self.assertEqual(len(results), 2)

        expected = [
            {
                'fields': ['event_hosts,featured,group_topics,plain_text_description,rsvpable,series'],
                'key': ['aaaa'],
                'order': ['updated'],
                'page': ['2'],
                'scroll': ['since:1970-01-01T00:00:00.000Z'],
                'sign': ['true'],
                'status': ['cancelled,upcoming,past,proposed,suggested']
            },
            {
                'key': ['aaaa'],
                'sign': ['true']
            }
        ]

        self.assertEqual(len(http_requests), 2)

        for x in range(0, len(http_requests)):
            req = http_requests[x]
            self.assertEqual(req.method, 'GET')
            self.assertRegex(req.path, '/sqlpass-es/events')
            self.assertDictEqual(req.querystring, expected[x])

    @httpretty.activate
    def test_sleep_for_rate_oauth_token(self):
        """ Test if the clients sleeps when the rate limit is reached when using an oauth token"""

        wait_to_reset = 1

        http_requests = setup_http_server(rate_limit=0,
                                          reset_rate_limit=wait_to_reset)

        client = MeetupClient('aaaa', is_oauth_token=True,
                              max_items=2,
                              min_rate_to_sleep=2,
                              sleep_for_rate=True)

        # Call API
        before = float(time.time())
        events = client.events('sqlpass-es')
        results = [event for event in events]
        after = float(time.time())
        diff = after - before

        self.assertGreaterEqual(diff, wait_to_reset)
        self.assertEqual(len(results), 2)

        expected = [
            {
                'fields': ['event_hosts,featured,group_topics,plain_text_description,rsvpable,series'],
                'access_token': ['aaaa'],
                'order': ['updated'],
                'page': ['2'],
                'scroll': ['since:1970-01-01T00:00:00.000Z'],
                'status': ['cancelled,upcoming,past,proposed,suggested']
            },
            {
                'access_token': ['aaaa']
            }
        ]

        self.assertEqual(len(http_requests), 2)

        for x in range(0, len(http_requests)):
            req = http_requests[x]
            self.assertEqual(req.method, 'GET')
            self.assertRegex(req.path, '/sqlpass-es/events')
            self.assertDictEqual(req.querystring, expected[x])

    @httpretty.activate
    def test_rate_limit_error(self):
        """Test if a rate limit error is raised when rate is exhausted"""

        http_requests = setup_http_server(rate_limit=0,
                                          reset_rate_limit=1)

        client = MeetupClient('aaaa', max_items=2)

        # Call API
        events = client.events('sqlpass-es')

        with self.assertRaises(RateLimitError):
            _ = [event for event in events]

        expected = {
            'fields': ['event_hosts,featured,group_topics,plain_text_description,rsvpable,series'],
            'key': ['aaaa'],
            'order': ['updated'],
            'page': ['2'],
            'scroll': ['since:1970-01-01T00:00:00.000Z'],
            'sign': ['true'],
            'status': ['cancelled,upcoming,past,proposed,suggested']
        }

        self.assertEqual(len(http_requests), 1)

        req = http_requests[0]
        self.assertEqual(req.method, 'GET')
        self.assertRegex(req.path, '/sqlpass-es/events')
        self.assertDictEqual(req.querystring, expected)

    @httpretty.activate
    def test_rate_limit_error_oauth_token(self):
        """Test if a rate limit error is raised when rate is exhausted for the oauth token"""

        http_requests = setup_http_server(rate_limit=0,
                                          reset_rate_limit=1)

        client = MeetupClient('aaaa', is_oauth_token=True, max_items=2)

        # Call API
        events = client.events('sqlpass-es')

        with self.assertRaises(RateLimitError):
            _ = [event for event in events]

        expected = {
            'fields': ['event_hosts,featured,group_topics,plain_text_description,rsvpable,series'],
            'access_token': ['aaaa'],
            'order': ['updated'],
            'page': ['2'],
            'scroll': ['since:1970-01-01T00:00:00.000Z'],
            'status': ['cancelled,upcoming,past,proposed,suggested']
        }

        self.assertEqual(len(http_requests), 1)

        req = http_requests[0]
        self.assertEqual(req.method, 'GET')
        self.assertRegex(req.path, '/sqlpass-es/events')
        self.assertDictEqual(req.querystring, expected)

    @httpretty.activate
    def test_too_many_requests(self):
        """Test if a Retry error is raised"""

        httpretty.register_uri(httpretty.GET,
                               MEETUP_EVENTS_URL,
                               status=429)

        client = MeetupClient('aaaa', max_items=2, sleep_time=0.1)
        start = float(time.time())
        expected = start + (sum([i * client.sleep_time for i in range(client.MAX_RETRIES)]))

        events = client.events('sqlpass-es')
        with self.assertRaises(requests.exceptions.RetryError):
            _ = [event for event in events]

        end = float(time.time())
        self.assertGreater(end, expected)

    def test_sanitize_for_archive(self):
        """Test whether the sanitize method works properly"""

        url = "http://example.com"
        headers = "headers-information"
        payload = {
            'page': 2,
            'sign': ('true',),
            'order': 'updated',
            'scroll': 'since:2016-01-01T00:00:00.000Z',
            'key': 'aaaa'
        }

        s_url, s_headers, s_payload = MeetupClient.sanitize_for_archive(url, headers, copy.deepcopy(payload))
        payload.pop('key')
        payload.pop('sign')

        self.assertEqual(url, s_url)
        self.assertEqual(headers, s_headers)
        self.assertEqual(payload, s_payload)

    def test_sanitize_for_archive_oauth_token(self):
        """Test whether the sanitize method works properly when using an oauth token"""

        url = "http://example.com"
        headers = "headers-information"
        payload = {
            'page': 2,
            'order': 'updated',
            'scroll': 'since:2016-01-01T00:00:00.000Z',
            'access_token': 'aaaa'
        }

        s_url, s_headers, s_payload = MeetupClient.sanitize_for_archive(url, headers, copy.deepcopy(payload))
        payload.pop('access_token')

        self.assertEqual(url, s_url)
        self.assertEqual(headers, s_headers)
        self.assertEqual(payload, s_payload)


if __name__ == "__main__":
    unittest.main(warnings='ignore')
