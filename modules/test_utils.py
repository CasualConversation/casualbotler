#!/usr/bin/env python3
from modules.utils import *
import pytest
import datetime
import requests

TEST_TIME = datetime.datetime(2012, 12, 25, 17, 5, 55)

@pytest.fixture
def patch_datetime_now(monkeypatch):
    class mydatetime:
        @classmethod
        def now(cls):
            return TEST_TIME
    monkeypatch.setattr(datetime, 'datetime', mydatetime)

def test_create_timestamp_file_name_now(patch_datetime_now):
    assert create_timestamp_file_name() == '20121225T170555z'
