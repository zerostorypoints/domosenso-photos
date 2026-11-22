import importlib
import json
import sys
import types
from datetime import date, datetime
from pathlib import Path

pkg = types.ModuleType('lightroom_filter_pkg')
pkg.__path__ = [str(Path(__file__).parents[1] / 'custom_components/domosenso_photos')]
sys.modules[pkg.__name__] = pkg
lc = importlib.import_module('lightroom_filter_pkg.lightroom_catalog')
lf = importlib.import_module('lightroom_filter_pkg.lightroom_filter')

TODAY = date(2026, 6, 15)


def meta(make=None, model=None, lens=None):
    return lc.CameraMeta(make, model, lens, None, None, None, None, None)


def photo(aid='a', rating=4, flag=None, captured=datetime(2025, 5, 4, 10, 0), edited=False,
          albums=('A1',), make=None, model=None, lens=None, width=None, height=None, keywords=()):
    return lc.Photo(aid, rating, flag, captured, '', edited, tuple(albums),
                    meta(make, model, lens), width, height, tuple(keywords))


def album(aid, name, parent=None, is_set=False):
    return lc.Album(aid, name, parent, is_set)


ALBUMS = {
    'A1': album('A1', 'Zebra'),
    'A2': album('A2', 'apple'),
    'S1': album('S1', 'Trips', is_set=True),
    'C1': album('C1', 'Alps', 'S1'),
    'S2': album('S2', 'Sub', 'S1', True),
    'C2': album('C2', 'Deep', 'S2'),
}


def run(photos, prefix='', today=TODAY, albums=ALBUMS, **kw):
    return lf.place(photos, lf.Filter(**kw), albums, prefix, today)


def ids(placed):
    return [p.photo.asset_id for p in placed]


def test_default_filter_is_three_stars_not_rejected():
    f = lf.Filter()
    assert f.ratings == frozenset({3, 4, 5}) and f.flag == 'not_rejected'
    ps = [photo('r2', rating=2), photo('r3', rating=3), photo('rej', rating=5, flag='reject'),
          photo('pick', rating=5, flag='pick')]
    assert ids(run(ps)) == ['r3', 'pick']


def test_ratings():
    ps = [photo('a', rating=0), photo('b', rating=1), photo('c', rating=5)]
    assert ids(run(ps, ratings=frozenset({0, 1}))) == ['a', 'b']


def test_flag_picks_only():
    ps = [photo('a', flag='pick'), photo('b'), photo('c', flag='reject')]
    assert ids(run(ps, flag='picks_only')) == ['a']
    assert ids(run(ps, flag='not_rejected')) == ['a', 'b']


def test_dates_inclusive():
    ps = [photo('a', captured=datetime(2025, 1, 1, 0, 0)), photo('b', captured=datetime(2025, 1, 31, 23, 59)),
          photo('c', captured=datetime(2025, 2, 1)), photo('z', captured=datetime(2024, 12, 31, 23, 59))]
    assert ids(run(ps, captured_from=date(2025, 1, 1), captured_to=date(2025, 1, 31))) == ['a', 'b']
    assert ids(run(ps, captured_from=date(2025, 1, 31))) == ['b', 'c']
    assert ids(run(ps, captured_to=date(2025, 1, 1))) == ['a', 'z']


def test_last_years_replaces_from_to():
    ps = [photo('old', captured=datetime(2020, 1, 1)), photo('new', captured=datetime(2025, 7, 1)),
          photo('edge', captured=datetime(2024, 6, 15))]
    assert ids(run(ps, captured_last_years=2, captured_from=date(2000, 1, 1),
                   captured_to=date(2001, 1, 1))) == ['new', 'edge']


def test_last_years_on_feb_29():
    ps = [photo('a', captured=datetime(2023, 2, 28)), photo('b', captured=datetime(2023, 2, 27))]
    assert ids(run(ps, today=date(2024, 2, 29), captured_last_years=1)) == ['a']


def test_edited_only():
    ps = [photo('a', edited=True), photo('b')]
    assert ids(run(ps, edited_only=True)) == ['a']
    assert ids(run(ps)) == ['a', 'b']


def test_equipment_include_substring_casefold():
    ps = [photo('a', make='NIKON CORP'), photo('b', model='Z6'), photo('c', lens='NIKKOR 50mm'),
          photo('d', make='Canon')]
    assert ids(run(ps, equipment_include=('nikon', 'z6'))) == ['a', 'b']
    assert ids(run(ps, equipment_include=('nikkor',))) == ['c']


def test_equipment_exclude():
    ps = [photo('a', make='NIKON CORP'), photo('b', lens='Sigma 35'), photo('c')]
    assert ids(run(ps, equipment_exclude=('nikon', 'SIGMA'))) == ['c']


def test_equipment_include_fails_without_meta():
    assert ids(run([photo('a')], equipment_include=('x',))) == []


def test_orientation():
    ps = [photo('l', width=300, height=200), photo('p', width=200, height=300),
          photo('s', width=200, height=200), photo('u')]
    assert ids(run(ps, orientation='landscape')) == ['l']
    assert ids(run(ps, orientation='portrait')) == ['p']
    assert ids(run(ps, orientation='any')) == ['l', 'p', 's', 'u']


def test_keywords():
    ps = [photo('a', keywords=('Sunset', 'Sea')), photo('b', keywords=('Sunset Beach',)), photo('c')]
    assert ids(run(ps, keywords_include=('sunset',))) == ['a']
    assert ids(run(ps, keywords_exclude=('SEA',))) == ['b', 'c']


def test_exclude_wins_over_include():
    ps = [photo('a', albums=('A1', 'A2'))]
    out = run(ps, albums_include=frozenset({'A1', 'A2'}), albums_exclude=frozenset({'A2'}))
    assert [p.key for p in out] == ['A1']
    assert run(ps, albums_include=frozenset({'A1'}), albums_exclude=frozenset({'A1'})) == []


def test_set_in_include_expands_to_children():
    ps = [photo('a', albums=('C1',)), photo('b', albums=('C2',)), photo('c', albums=('A1',))]
    assert ids(run(ps, albums_include=frozenset({'S1'}))) == ['a', 'b']


def test_set_in_exclude_expands():
    ps = [photo('a', albums=('C2',)), photo('b', albums=('A1',))]
    assert ids(run(ps, albums_exclude=frozenset({'S1'}))) == ['b']


def test_photo_only_in_excluded_album_is_dropped_not_unfiled():
    ps = [photo('a', albums=('A2',))]
    assert run(ps, albums_exclude=frozenset({'A2'}), include_unfiled=True) == []


def test_unfiled_goes_to_year_month():
    ps = [photo('a', albums=(), captured=datetime(2025, 5, 4))]
    out = run(ps)
    assert [(p.key, p.folder) for p in out] == [('2025-05', '2025-05')]
    assert run(ps, include_unfiled=False) == []


def test_include_with_only_missing_ids_passes_no_album_photo():
    ps = [photo('a', albums=()), photo('b', albums=('A1',))]
    out = run(ps, albums_include=frozenset({'GONE'}))
    assert ids(out) == ['a', 'b'] and out[0].key == '2025-05' and out[1].key == 'A1'


def test_album_not_in_albums_is_not_eligible():
    ps = [photo('a', albums=('UNKNOWN',))]
    assert run(ps) == []


def test_set_membership_not_eligible():
    # a photo's membership of a set is not a collection
    ps = [photo('a', albums=('S1',))]
    assert run(ps) == []


def test_first_album_by_casefold_name_then_id():
    ps = [photo('a', albums=('A1', 'A2'))]
    assert run(ps)[0].key == 'A2'
    alb = {'X2': album('X2', 'Same'), 'X1': album('X1', 'same')}
    assert run([photo('a', albums=('X2', 'X1'))], albums=alb)[0].key == 'X1'


def test_folder_name_only_symbols_is_album():
    assert lf.folder_name('@') == 'album'
    assert lf.folder_name('') == 'album'


def test_folder_name_strips_leading_at():
    assert lf.folder_name('@eaDir') == 'eaDir'


def test_prefix_joined():
    ps = [photo('a', albums=('A1',))]
    assert run(ps, prefix='Lightroom')[0].folder == 'Lightroom/Zebra'
    assert run(ps)[0].folder == 'Zebra'
    assert run([photo('a', albums=())], prefix='LR')[0].folder == 'LR/2025-05'


def test_input_order_kept():
    ps = [photo('b', albums=('A2',)), photo('a', albums=('A1',))]
    assert ids(run(ps)) == ['b', 'a']


def test_options_round_trip():
    f = lf.Filter(ratings=frozenset({1, 5}), flag='picks_only', albums_include=frozenset({'x', 'y'}),
                  albums_exclude=frozenset({'z'}), include_unfiled=False, captured_from=date(2020, 1, 2),
                  captured_to=date(2021, 3, 4), captured_last_years=3, edited_only=True,
                  equipment_include=('a', 'b'), equipment_exclude=('c',), orientation='portrait',
                  keywords_include=('k',), keywords_exclude=('l', 'm'))
    opts = lf.filter_to_options(f)
    assert json.loads(json.dumps(opts)) == opts
    assert lf.filter_from_options(opts) == f
    assert lf.filter_from_options(lf.filter_to_options(lf.Filter())) == lf.Filter()


def test_from_options_ignores_unknown_and_fills_defaults():
    assert lf.filter_from_options({'bogus': 1}) == lf.Filter()
    assert lf.filter_from_options({'ratings': [5]}).ratings == frozenset({5})


def test_flag_any_keeps_rejected():
    ps = [photo('a', flag='reject'), photo('b', flag='pick'), photo('c')]
    assert ids(run(ps, flag='any')) == ['a', 'b', 'c']


def test_from_options_unknown_flag_and_orientation_use_defaults():
    f = lf.filter_from_options({'flag': 'bogus', 'orientation': 'diagonal'})
    assert f.flag == 'not_rejected' and f.orientation == 'any'
    assert lf.filter_from_options({'flag': 'any'}).flag == 'any'


def test_folder_is_the_album_path():
    assert run([photo('a', albums=('C2',))])[0].folder == 'Trips/Sub/Deep'
    assert run([photo('a', albums=('C2',))], prefix='LR')[0].folder == 'LR/Trips/Sub/Deep'
    assert run([photo('a', albums=('A1',))])[0].folder == 'Zebra'


def test_album_path_segments_are_safe():
    albums = {'S': album('S', 'a/b', is_set=True), 'C': album('C', '@x', 'S')}
    assert run([photo('a', albums=('C',))], albums=albums)[0].folder == 'a_b/x'


def test_album_named_like_a_set_takes_a_suffix():
    albums = {'S': album('S', 'Iceland', is_set=True), 'C': album('C', 'Day', 'S'), 'D': album('D', 'Iceland')}
    out = run([photo('a', albums=('C',)), photo('b', albums=('D',))], albums=albums)
    assert [p.folder for p in out] == ['Iceland/Day', 'Iceland-2']


def test_missing_parent_ends_the_path():
    albums = {'C': album('C', 'Lonely', 'gone')}
    assert run([photo('a', albums=('C',))], albums=albums)[0].folder == 'Lonely'
