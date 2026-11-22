import copy
import importlib
import json
import sys
import types
from datetime import datetime
from pathlib import Path

pkg = types.ModuleType('lightroom_catalog_pkg')
pkg.__path__ = [str(Path(__file__).parents[1] / 'custom_components/domosenso_photos')]
sys.modules[pkg.__name__] = pkg
lc = importlib.import_module('lightroom_catalog_pkg.lightroom_catalog')

FIX = Path(__file__).parent / 'fixtures' / 'lightroom'


def _load(name):
    return json.loads((FIX / name).read_text())


def test_to_photo_full():
    p = lc.to_photo(_load('asset_full.json'), ['alb-rome'])
    assert p.asset_id == 'a1b2c3d4e5f60718293a4b5c6d7e8f90'
    assert p.rating == 4 and p.flag == 'pick'
    assert p.captured == datetime(2024, 5, 3, 17, 3, 26)
    assert p.meta.make == 'Fabrikam' and p.meta.model == 'FX-100'
    assert p.meta.lens == 'Fabrikam 35mm F1.8'
    assert p.meta.focal_mm == 35.0 and p.meta.focal35_mm is None
    assert p.meta.f_number == 1.8 and p.meta.exposure_s == 0.0025 and p.meta.iso == 200
    assert p.edited is True
    assert p.changed == '2024-05-06T07:15:00Z|2024-05-05T10:00:00Z'
    assert p.album_ids == ('alb-rome',)


def test_to_photo_latest_rating_wins():
    a = _load('asset_full.json')
    a['payload']['ratings'] = {
        'old-app': {'rating': 5, 'date': '2024-01-01T00:00:00Z'},
        'new-app': {'rating': 2, 'date': '2024-03-01T00:00:00Z'},
    }
    assert lc.to_photo(a, []).rating == 2


def test_to_photo_bare():
    a = _load('asset_bare.json')
    p = lc.to_photo(a, [])
    assert p.rating == 0 and p.flag is None
    assert p.captured == datetime(2024, 6, 1, 11, 45, 30)
    assert p.edited is False
    assert p.changed == '2024-06-02T12:00:00Z'  # updated
    assert p.meta == lc.CameraMeta(None, None, None, None, None, None, None, None)
    a2 = copy.deepcopy(a)
    a2['payload']['userUpdated'] = '2024-06-03T00:00:00Z'
    assert lc.to_photo(a2, []).changed == '|2024-06-03T00:00:00Z'
    a2['payload']['develop'] = {'userUpdated': '2024-06-04T00:00:00Z'}
    assert lc.to_photo(a2, []).changed == '2024-06-04T00:00:00Z|2024-06-03T00:00:00Z'


def test_to_photo_dimensions_and_keywords():
    # Task 1 record not yet available: dimensions and keywords are not read.
    p = lc.to_photo(_load('asset_full.json'), [])
    assert p.width is None and p.height is None and p.keywords == ()


def test_to_album_set_and_collection():
    by_id = {r['id']: lc.to_album(r) for r in _load('albums.json')['resources']}
    assert by_id['set-travel'] == lc.Album('set-travel', 'Travel', None, True)
    assert by_id['alb-rome'] == lc.Album('alb-rome', 'Rome', 'set-2024', False)
    assert by_id['alb-loose'].parent_id is None and not by_id['alb-loose'].is_set


def test_album_label_nested():
    albums = {a.id: a for a in map(lc.to_album, _load('albums.json')['resources'])}
    assert lc.album_label('alb-rome', albums) == 'Travel / 2024 / Rome'
    assert lc.album_label('alb-loose', albums) == 'Loose'
    assert lc.album_label('gone', albums) == 'gone'


def test_photo_dict_round_trip():
    p = lc.to_photo(_load('asset_full.json'), ['alb-rome', 'alb-loose'])
    d = lc.photo_to_dict(p)
    assert json.loads(json.dumps(d)) == d
    assert lc.photo_from_dict(d) == p


def test_edited_from_defaults_false_is_edited():
    a = _load('asset_full.json')
    a['payload']['develop'] = {'fromDefaults': False}  # no edit date needed
    assert lc.to_photo(a, []).edited is True
    a['payload']['develop'] = {'fromDefaults': True, 'userUpdated': '2024-05-06T07:15:00Z'}
    assert lc.to_photo(a, []).edited is False  # userUpdated is on unedited assets too


def test_edited_without_from_defaults_falls_back_to_user_updated():
    a = _load('asset_full.json')
    a['payload']['develop'] = {'crsHDREditMode': False}
    assert lc.to_photo(a, []).edited is False
    a['payload']['develop'] = {'userUpdated': '2024-05-06T07:15:00Z'}
    assert lc.to_photo(a, []).edited is True


def test_rating_not_a_number_is_unrated():
    a = _load('asset_full.json')
    for value in ('four', None, [], 'nan', {'x': 1}):
        a['payload']['ratings'] = {'app': {'rating': value, 'date': '2024-01-01T00:00:00Z'}}
        assert lc.to_photo(a, []).rating == 0
    a['payload']['ratings'] = {'app': {'rating': '3', 'date': '2024-01-01T00:00:00Z'}}
    assert lc.to_photo(a, []).rating == 3


def test_latest_with_mixed_date_types():
    a = _load('asset_full.json')
    a['payload']['ratings'] = {'one': {'rating': 5, 'date': 20240101},
                               'two': {'rating': 2, 'date': '2024-03-01T00:00:00Z'}}
    a['payload']['reviews'] = {'one': {'flag': 'pick', 'date': None}, 'two': {'flag': 'reject', 'date': 7}}
    p = lc.to_photo(a, [])
    assert p.rating == 5 and p.flag == 'reject'  # compared as text: '20240101' > '2024-03-01...'
