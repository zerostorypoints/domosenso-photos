"""Lightroom plan and execution on owned folders (no Home Assistant import)."""
import asyncio
import errno
import importlib
import io
import json
import os
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

pkg = types.ModuleType('lightroom_sync_pkg')
pkg.__path__ = [str(Path(__file__).parents[1] / 'custom_components/domosenso_photos')]
sys.modules[pkg.__name__] = pkg
lrs = importlib.import_module('lightroom_sync_pkg.lightroom_sync')
catalog = importlib.import_module('lightroom_sync_pkg.lightroom_catalog')
lrf = importlib.import_module('lightroom_sync_pkg.lightroom_filter')
layout = importlib.import_module('lightroom_sync_pkg.album_layout')
files = importlib.import_module('lightroom_sync_pkg.album_files')

OWNER = lrs.owner_for('acct-0001')
NOW = 50_000
_META = catalog.CameraMeta(None, None, None, None, None, None, None, None)


def _jpeg():
    buf = io.BytesIO()
    Image.new('RGB', (16, 12), (200, 80, 40)).save(buf, format='JPEG')
    return buf.getvalue()


JPEG = _jpeg()


def photo(n, changed='c1', captured=None):
    return catalog.Photo(f'as{n:03}', 4, None, captured or datetime(2025, 6, 1 + n % 28, 10, 0), changed,
                         False, (), _META)


def placed(n, key='alb1', folder='Trip', **kw):
    return lrf.Placed(photo(n, **kw), key, folder)


def owned(key, path, *ns, changed='c1'):
    items = {f'as{n:03}': {'name': lrs.file_name(placed(n)), 'changed': changed} for n in ns}
    return lrs.OwnedFolder(key, path, items)


def plan(placed_list, folders, listed=None, confirm=None):
    by_key = lrs.Owned({f.key: f for f in folders})
    return lrs.plan(placed_list, by_key, len(placed_list) if listed is None else listed, confirm)


async def _direct(fn):
    return fn()


async def _fetch(p):
    return JPEG


def execute(pl, photo_dir, fetch=_fetch, free_floor=0, prefix=''):
    return asyncio.run(lrs.execute(pl, photo_dir, OWNER, fetch, _direct, free_floor, timezone.utc, NOW, prefix=prefix))


def run(photo_dir, placed_list, prefix='', listed=None, confirm=None, **kw):
    folders = lrs.read_owned(photo_dir, prefix, OWNER)
    pl = lrs.plan(placed_list, folders, len(placed_list) if listed is None else listed, confirm)
    return pl, execute(pl, photo_dir, prefix=prefix, **kw)


def marker(folder):
    return json.loads((folder / '.sync.json').read_text())


def photos(folder):
    return sorted(p.name for p in folder.iterdir() if p.name != '.sync.json')


def ids(items):
    return sorted(p.photo.asset_id for p in items)


# --- plan -------------------------------------------------------------------------------------

def test_file_name():
    assert lrs.file_name(placed(3)) == '2025-06-04_as003.jpg'


def test_plan_add_update_move_remove():
    folders = [owned('alb1', 'Trip', 1, 2, 3), owned('alb2', 'Beach', 4)]
    pl = plan([placed(1), placed(2, changed='c2'), placed(4), placed(5)], folders)
    assert ids(pl.add) == ['as005']
    assert ids(pl.update) == ['as002']
    assert [(p.photo.asset_id, old) for p, old in pl.move] == [('as004', 'alb2')]
    assert pl.remove == [('alb1', 'as003')]
    assert pl.rename == [] and pl.hold is None


def _many(n):
    return [owned('alb1', 'Trip', *range(n))]


def test_plan_mass_remove_holds():
    keep = [placed(n) for n in range(9)]
    pl = plan(keep, _many(20))
    assert pl.hold == 'mass_remove' and pl.remove == [] and pl.move == []


def test_plan_mass_remove_under_20_does_not_hold():
    pl = plan([placed(0)], _many(19))
    assert pl.hold is None and len(pl.remove) == 18


def test_plan_exactly_half_removed_does_not_hold():
    pl = plan([placed(n) for n in range(10)], _many(20))
    assert pl.hold is None and len(pl.remove) == 10


def test_plan_confirm_releases_hold():
    pl = plan([placed(0)], _many(20), confirm='mass_remove')
    assert pl.hold is None and len(pl.remove) == 19


def test_plan_mass_unfile_holds():
    unfiled = [placed(n, key='2025-06', folder='2025-06') for n in range(20)]
    pl = plan(unfiled, _many(20))
    assert pl.hold == 'mass_unfile' and pl.move == [] and pl.remove == [] and pl.add == []
    released = plan(unfiled, _many(20), confirm='mass_unfile')
    assert released.hold is None and len(released.move) == 20


def test_plan_moves_between_albums_do_not_unfile_hold():
    pl = plan([placed(n, key='alb2', folder='Beach') for n in range(20)], _many(20))
    assert pl.hold is None and len(pl.move) == 20


def test_plan_empty_library_holds():
    pl = plan([], [owned('alb1', 'Trip', 1)], listed=0)
    assert pl.hold == 'empty_library' and pl.remove == []
    assert plan([], [], listed=0).hold is None


def test_plan_hold_keeps_adds_and_updates():
    keep = [placed(n) for n in range(5)] + [placed(5, changed='c2'), placed(30)]
    pl = plan(keep, _many(20))
    assert pl.hold == 'mass_remove'
    assert ids(pl.add) == ['as030'] and ids(pl.update) == ['as005']
    assert pl.remove == [] and pl.move == []


def test_plan_asset_in_two_owned_folders_removes_stale_copy():
    folders = [owned('alb1', 'Trip', 1), owned('alb2', 'Beach', 1)]
    pl = plan([placed(1)], folders)
    assert pl.remove == [('alb2', 'as001')] and pl.move == [] and pl.add == [] and pl.hold is None
    # stale copies do not count towards a hold: 20 files owned, 20 stale copies removed
    big = [owned('alb1', 'Trip', *range(20)), owned('alb2', 'Beach', *range(20))]
    pl = plan([placed(n) for n in range(20)], big)
    assert pl.hold is None and len(pl.remove) == 20


def test_read_owned_same_key_copy_is_left_alone(tmp_path):
    run(tmp_path, [placed(1), placed(2)])
    # the user copies the folder, marker included
    (tmp_path / 'Copy').mkdir()
    for f in (tmp_path / 'Trip').iterdir():
        (tmp_path / 'Copy' / f.name).write_bytes(f.read_bytes())
    before = {f.name: f.read_bytes() for f in (tmp_path / 'Copy').iterdir()}
    folders = lrs.read_owned(tmp_path, '', OWNER)
    assert folders == {'alb1': lrs.OwnedFolder('alb1', 'Trip', marker(tmp_path / 'Trip')['items'])}
    # a move out of the album, a removal, an add and an update: the copy is never touched
    run(tmp_path, [placed(1, key='alb2', folder='Beach'), placed(3)])
    run(tmp_path, [placed(3, changed='c2')])
    assert photos(tmp_path / 'Trip') == ['2025-06-04_as003.jpg']
    assert {f.name: f.read_bytes() for f in (tmp_path / 'Copy').iterdir()} == before


def _copy(src, dst):
    dst.mkdir()
    for f in src.iterdir():
        (dst / f.name).write_bytes(f.read_bytes())
    return {f.name: f.read_bytes() for f in dst.iterdir()}


def test_read_owned_byte_identical_copy_is_ignored(tmp_path):
    run(tmp_path, [placed(1), placed(2)])
    before = _copy(tmp_path / 'Trip', tmp_path / 'Copy')  # cp / Finder / File Station
    assert {f.path for f in lrs.read_owned(tmp_path, '', OWNER).values()} == {'Trip'}
    run(tmp_path, [placed(1), placed(2)])  # a run with no changes
    pl, _ = run(tmp_path, [placed(1, folder='Coast')])  # album renamed; 'Copy' sorts before 'Trip'
    assert pl.rename == [('alb1', 'Trip', 'Coast')]
    assert photos(tmp_path / 'Coast') == ['2025-06-02_as001.jpg'] and not (tmp_path / 'Trip').exists()
    assert marker(tmp_path / 'Coast')['path'] == 'Coast'
    assert {f.name: f.read_bytes() for f in (tmp_path / 'Copy').iterdir()} == before


def test_read_owned_moved_original_is_adopted(tmp_path):
    run(tmp_path, [placed(1)])
    (tmp_path / 'Trip').rename(tmp_path / 'Moved')  # by the user; nothing of ours is left at 'Trip'
    folders = lrs.read_owned(tmp_path, '', OWNER)
    assert {k: f.path for k, f in folders.items()} == {'alb1': 'Moved'}
    assert marker(tmp_path / 'Moved')['path'] == 'Trip'
    _, res = run(tmp_path, [placed(1, folder='Moved'), placed(3, folder='Moved')])
    assert res.added == 1
    assert photos(tmp_path / 'Moved') == ['2025-06-02_as001.jpg', '2025-06-04_as003.jpg']
    assert marker(tmp_path / 'Moved')['path'] == 'Moved'


def test_execute_enospc_on_write_is_disk_full(tmp_path, monkeypatch):
    def full(folder, name, data, mtime):
        raise OSError(errno.ENOSPC, 'No space left on device')

    monkeypatch.setattr(lrs, 'write_photo', full)
    with pytest.raises(lrs.DiskFull):
        run(tmp_path, [placed(1)])


def test_two_self_recorded_folders_are_ambiguous(tmp_path):
    for name in ('A', 'B'):  # older markers without a path: both count as self-recorded
        (tmp_path / name).mkdir()
        (tmp_path / name / '.sync.json').write_text(json.dumps({'owner': OWNER, 'key': 'alb1', 'items': {}}))
    folders = lrs.read_owned(tmp_path, '', OWNER)
    assert folders == {} and folders.ambiguous == [('alb1', ['A', 'B'])]


def _tree(folder):
    return {f.name: f.read_bytes() for f in folder.iterdir()}


def test_copy_survives_two_album_renames(tmp_path):
    run(tmp_path, [placed(1), placed(2)])
    before = _copy(tmp_path / 'Trip', tmp_path / 'Copy')
    run(tmp_path, [placed(1, folder='Zebra'), placed(2, folder='Zebra')])  # first rename
    assert photos(tmp_path / 'Zebra') == ['2025-06-02_as001.jpg', '2025-06-03_as002.jpg']
    # the copy still records 'Trip' and nothing of ours is there now; Zebra records itself
    assert {k: f.path for k, f in lrs.read_owned(tmp_path, '', OWNER).items()} == {'alb1': 'Zebra'}
    pl, _ = run(tmp_path, [placed(1, folder='Apple')])  # second rename; 'Apple' < 'Copy' < 'Zebra'
    assert pl.rename == [('alb1', 'Zebra', 'Apple')] and pl.remove == [('alb1', 'as002')]
    assert photos(tmp_path / 'Apple') == ['2025-06-02_as001.jpg'] and not (tmp_path / 'Zebra').exists()
    assert _tree(tmp_path / 'Copy') == before
    assert {k: f.path for k, f in lrs.read_owned(tmp_path, '', OWNER).items()} == {'alb1': 'Apple'}


def test_moved_and_copied_folder_is_ambiguous_and_untouched(tmp_path):
    run(tmp_path, [placed(1), placed(2)])
    (tmp_path / 'Trip').rename(tmp_path / 'Moved')
    _copy(tmp_path / 'Moved', tmp_path / 'Copy')
    trees = {n: _tree(tmp_path / n) for n in ('Moved', 'Copy')}
    folders = lrs.read_owned(tmp_path, '', OWNER)
    assert folders == {} and folders.ambiguous == [('alb1', ['Copy', 'Moved'])]
    pl, res = run(tmp_path, [placed(1, folder='Coast'), placed(3)], listed=5)
    assert pl.ambiguous == [('alb1', ['Copy', 'Moved'])]
    assert pl.add == pl.update == pl.move == pl.remove == pl.rename == [] and pl.hold is None
    assert (res.added, res.updated, res.moved, res.removed) == (0, 0, 0, 0)
    assert {n: _tree(tmp_path / n) for n in ('Moved', 'Copy')} == trees
    assert sorted(p.name for p in tmp_path.iterdir()) == ['Copy', 'Moved']
    _, res = run(tmp_path, [], listed=0)  # not even an empty library touches them
    assert {n: _tree(tmp_path / n) for n in ('Moved', 'Copy')} == trees


def test_ambiguous_key_photos_not_added_elsewhere(tmp_path):
    run(tmp_path, [placed(1, key='alb2', folder='Beach')])
    for name in ('A', 'B'):
        (tmp_path / name).mkdir()
        (tmp_path / name / '.sync.json').write_text(json.dumps({'owner': OWNER, 'key': 'alb1', 'items': {}}))
    beach = _tree(tmp_path / 'Beach')
    # as001 now belongs to the ambiguous album, as005 is new in it: neither moves, is removed or added
    pl, res = run(tmp_path, [placed(1, folder='Trip'), placed(5, folder='Trip')])
    assert pl.add == pl.move == pl.remove == [] and res.added == res.moved == res.removed == 0
    assert _tree(tmp_path / 'Beach') == beach
    assert sorted(p.name for p in tmp_path.iterdir()) == ['A', 'B', 'Beach']


def test_plan_album_rename_renames_folder():
    pl = plan([placed(1, folder='Lr/Coast')], [owned('alb1', 'Lr/Trip', 1)])
    assert pl.rename == [('alb1', 'Lr/Trip', 'Lr/Coast')]
    assert pl.add == pl.update == pl.move == pl.remove == []


def test_suffixed_folder_stays_while_the_name_is_taken(tmp_path):
    (tmp_path / 'Trip').mkdir()  # the user's own folder
    run(tmp_path, [placed(1)])
    pl, _ = run(tmp_path, [placed(1), placed(2)])
    assert pl.rename == [('alb1', 'Trip-2', 'Trip')]  # offered; execute keeps the first free suffix
    assert photos(tmp_path / 'Trip-2') == ['2025-06-02_as001.jpg', '2025-06-03_as002.jpg']
    assert list((tmp_path / 'Trip').iterdir()) == []


def test_digit_suffix_in_a_name_is_not_a_collision_suffix(tmp_path):
    run(tmp_path, [placed(1, folder='Trip-2024')])
    run(tmp_path, [placed(1, folder='Trip')])  # album renamed from 'Trip-2024' to 'Trip'
    assert not (tmp_path / 'Trip-2024').exists()
    assert photos(tmp_path / 'Trip') == ['2025-06-02_as001.jpg']
    assert marker(tmp_path / 'Trip')['path'] == 'Trip'


# --- read_owned / execute ---------------------------------------------------------------------

def test_execute_writes_file_and_marker(tmp_path):
    p = placed(1, folder='Lr/Trip')
    pl, res = run(tmp_path, [p], prefix='Lr')
    folder = tmp_path / 'Lr/Trip'
    assert photos(folder) == ['2025-06-02_as001.jpg']
    assert (folder / '2025-06-02_as001.jpg').read_bytes() == JPEG
    assert os.stat(folder / '2025-06-02_as001.jpg').st_mtime == datetime(2025, 6, 2, 10, tzinfo=timezone.utc).timestamp()
    assert marker(folder) == {'owner': OWNER, 'kind': 'lightroom', 'key': 'alb1', 'path': 'Lr/Trip',
                              'items': {'as001': {'name': '2025-06-02_as001.jpg', 'changed': 'c1'}},
                              'last_sync': NOW}
    assert (res.added, res.updated, res.moved, res.removed, res.skipped) == (1, 0, 0, 0, [])
    owned_now = lrs.read_owned(tmp_path, 'Lr', OWNER)
    assert owned_now == {'alb1': lrs.OwnedFolder('alb1', 'Lr/Trip', marker(folder)['items'])}
    pl, res = run(tmp_path, [placed(1, folder='Lr/Trip', changed='c2')], prefix='Lr')
    assert (res.added, res.updated) == (0, 1)
    assert marker(folder)['items']['as001']['changed'] == 'c2'


def test_execute_move_writes_new_before_removing_old(tmp_path, monkeypatch):
    run(tmp_path, [placed(1), placed(2)])
    old = tmp_path / 'Trip/2025-06-02_as001.jpg'
    seen = []
    real = files.write_photo

    def spy(folder, name, data, mtime):
        seen.append((folder.name, old.exists()))
        real(folder, name, data, mtime)

    monkeypatch.setattr(lrs, 'write_photo', spy)
    _, res = run(tmp_path, [placed(1, key='alb2', folder='Beach'), placed(2)])
    assert seen == [('Beach', True)]
    assert res.moved == 1
    assert photos(tmp_path / 'Beach') == ['2025-06-02_as001.jpg']
    assert photos(tmp_path / 'Trip') == ['2025-06-03_as002.jpg']
    assert list(marker(tmp_path / 'Trip')['items']) == ['as002']


def test_execute_empty_folder_removed(tmp_path):
    run(tmp_path, [placed(1), placed(2, key='alb2', folder='Beach')])
    (tmp_path / 'Beach/mine.txt').write_text('x')
    _, res = run(tmp_path, [placed(1, key='alb3', folder='Hills')])
    assert res.removed == 1 and res.moved == 1
    assert not (tmp_path / 'Trip').exists()
    assert sorted(os.listdir(tmp_path / 'Beach')) == ['.sync.json', 'mine.txt']
    assert marker(tmp_path / 'Beach')['items'] == {}


def test_execute_skipped_fetch_counts(tmp_path):
    async def fetch(p):
        return None if p.photo.asset_id == 'as002' else JPEG

    _, res = run(tmp_path, [placed(1), placed(2)], fetch=fetch)
    assert (res.added, res.skipped) == (1, ['as002'])
    assert list(marker(tmp_path / 'Trip')['items']) == ['as001']


def test_execute_skipped_move_keeps_old_copy(tmp_path):
    run(tmp_path, [placed(1)])

    async def fetch(p):
        return None

    _, res = run(tmp_path, [placed(1, key='alb2', folder='Beach')], fetch=fetch)
    assert res.moved == 0 and res.skipped == ['as001']
    assert photos(tmp_path / 'Trip') == ['2025-06-02_as001.jpg']
    assert not (tmp_path / 'Beach').exists()


def test_execute_disk_floor_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(lrs, 'free_bytes', lambda path: 10)
    with pytest.raises(lrs.DiskFull):
        run(tmp_path, [placed(1)], free_floor=11)
    assert list(tmp_path.iterdir()) == []


def test_execute_photo_dir_missing_touches_nothing(tmp_path):
    gone = tmp_path / 'unmounted'
    pl = plan([placed(1, folder='Lr/Coast')], [owned('alb1', 'Lr/Trip', 2)])
    calls = []

    async def fetch(p):
        calls.append(p)
        return JPEG

    with pytest.raises(layout.PhotoDirMissing):
        asyncio.run(lrs.execute(pl, gone, OWNER, fetch, _direct, 0, timezone.utc, NOW))
    assert not gone.exists() and calls == []


def test_execute_never_touches_unowned_folder(tmp_path):
    run(tmp_path, [placed(1), placed(2)])
    # the user replaces the folder's marker by hand: the folder is no longer ours
    foreign = tmp_path / 'Trip'
    stale = lrs.read_owned(tmp_path, '', OWNER)
    (foreign / '.sync.json').write_text(json.dumps({'owner': 'someone-else', 'key': 'alb1', 'items': {}}))
    pl = lrs.plan([placed(3, key='alb2', folder='Beach'), placed(1, key='alb2', folder='Beach')], stale, 2, None)
    pl = lrs.LrPlan(pl.add, pl.update, pl.move, pl.remove + [('alb1', 'as002')],
                    [('alb1', 'Trip', 'Renamed')], None, pl.owned)
    execute(pl, tmp_path)
    assert photos(foreign) == ['2025-06-02_as001.jpg', '2025-06-03_as002.jpg']
    assert not (tmp_path / 'Renamed').exists()
    # a Synology folder and a hand folder next to the prefix are not read as ours
    (tmp_path / 'syn').mkdir()
    (tmp_path / 'syn/.sync.json').write_text(json.dumps({'album_id': 'a1', 'items': {'as009': 'x.jpg'}}))
    assert set(lrs.read_owned(tmp_path, '', OWNER)) == {'alb2'}


def test_execute_rename_moves_folder_and_keeps_files(tmp_path):
    run(tmp_path, [placed(1, folder='Lr/Trip')], prefix='Lr')
    pl, res = run(tmp_path, [placed(1, folder='Lr/Coast'), placed(2, folder='Lr/Coast')], prefix='Lr')
    assert pl.rename == [('alb1', 'Lr/Trip', 'Lr/Coast')]
    assert not (tmp_path / 'Lr/Trip').exists()
    assert photos(tmp_path / 'Lr/Coast') == ['2025-06-02_as001.jpg', '2025-06-03_as002.jpg']
    assert res.added == 1


def test_execute_rename_never_overwrites(tmp_path):
    run(tmp_path, [placed(1)])
    (tmp_path / 'Coast').mkdir()
    (tmp_path / 'Coast/mine.jpg').write_bytes(b'x')
    run(tmp_path, [placed(1, folder='Coast')])
    assert os.listdir(tmp_path / 'Coast') == ['mine.jpg']
    assert photos(tmp_path / 'Coast-2') == ['2025-06-02_as001.jpg']


def test_two_albums_same_clean_name_stable_suffix(tmp_path):
    both = [placed(1, key='alb1', folder='Trip'), placed(2, key='alb2', folder='Trip')]
    run(tmp_path, both)
    first = {f.key: f.path for f in lrs.read_owned(tmp_path, '', OWNER).values()}
    pl, _ = run(tmp_path, list(reversed(both)))
    second = {f.key: f.path for f in lrs.read_owned(tmp_path, '', OWNER).values()}
    assert first == second == {'alb1': 'Trip', 'alb2': 'Trip-2'}
    assert pl.add == [] and pl.move == [] and pl.remove == []


def test_execute_stale_copy_removed_after_kept(tmp_path):
    run(tmp_path, [placed(1), placed(2, key='alb2', folder='Beach')])
    # damaged state: a copy of as001 also listed in Beach
    beach = tmp_path / 'Beach'
    m = marker(beach)
    m['items']['as001'] = {'name': '2025-06-02_as001.jpg', 'changed': 'c1'}
    (beach / '.sync.json').write_text(json.dumps(m))
    (beach / '2025-06-02_as001.jpg').write_bytes(JPEG)
    pl, res = run(tmp_path, [placed(1), placed(2, key='alb2', folder='Beach')])
    assert pl.remove == [('alb2', 'as001')]
    assert photos(beach) == ['2025-06-03_as002.jpg']
    assert photos(tmp_path / 'Trip') == ['2025-06-02_as001.jpg']


def test_preview_counts_and_top_folders():
    wanted = [placed(1, 'alA', 'A'), placed(2, 'alA', 'A'), placed(3, 'alA', 'A'),  # adds
              placed(7, 'alB', 'B'),  # kept where it is
              placed(10, 'alA', 'A')]  # moves from C into A
    folders = lrs.Owned({'alB': owned('alB', 'B', 5, 6, 7), 'alC': owned('alC', 'C', 10)})
    out = lrs.preview(wanted, folders)
    assert {k: out[k] for k in ('kept', 'add', 'remove', 'move', 'hold')} == \
        {'kept': 1, 'add': 3, 'remove': 2, 'move': 1, 'hold': None}
    assert out['top'][0] == {'folder': 'A', 'add': 4, 'remove': 0}
    assert {t['folder']: (t['add'], t['remove']) for t in out['top']} == {'A': (4, 0), 'B': (0, 2), 'C': (0, 1)}


def test_preview_top_keeps_five_most_changed():
    wanted = [placed(n, f'al{n}', f'F{n}') for n in range(1, 8)]
    wanted += [placed(20 + n, 'al1', 'F1') for n in range(3)]
    out = lrs.preview(wanted, lrs.Owned())
    assert len(out['top']) == 5 and out['top'][0] == {'folder': 'F1', 'add': 4, 'remove': 0}


def test_preview_reports_hold():
    folders = lrs.Owned({'alB': owned('alB', 'B', *range(30, 30 + lrs.MASS_MIN))})
    out = lrs.preview([], folders)
    # The counts are what Save then a confirmation would do; the hold says the sync would wait first.
    assert out['hold'] == 'mass_remove' and out['remove'] == lrs.MASS_MIN and out['kept'] == 0
    assert lrs.preview([], folders, listed_assets=0)['hold'] == 'empty_library'


def test_collisions_lists_suffixed_folders_only():
    wanted = [placed(1, 'alA', 'lr/Trip'), placed(2, 'alB', 'lr/Lake'), placed(3, 'alC', 'lr/Sea-2')]
    folders = {'alA': owned('alA', 'lr/Trip-2', 1), 'alB': owned('alB', 'lr/Lake', 2),
               'alC': owned('alC', 'lr/Sea-2', 3), 'alD': owned('alD', 'lr/Old-3')}
    assert lrs.collisions(wanted, folders) == ['lr/Trip-2']


def test_owner_names_the_account_by_a_short_hash():
    import hashlib
    assert lrs.owner_for('acct-0001') == 'lightroom:' + hashlib.sha256(b'acct-0001').hexdigest()[:16]
    assert lrs.owner_for('acct-0001') != lrs.owner_for('acct-0002')


def test_plan_requires_owned():
    with pytest.raises(TypeError):
        lrs.plan([placed(1)], {'alb1': owned('alb1', 'Trip', 1)}, 1, None)


def test_plan_confirm_releases_only_the_same_hold():
    pl = plan([], _many(20), listed=0, confirm='mass_remove')  # confirmed mass_remove, then Adobe lists nothing
    assert pl.hold == 'empty_library' and pl.remove == [] and pl.move == []
    pl = plan([placed(0)], _many(20), confirm='mass_unfile')
    assert pl.hold == 'mass_remove' and pl.remove == []


def test_plan_held_renames_nothing():
    pl = plan([placed(0, folder='Coast')], _many(20))
    assert pl.hold == 'mass_remove' and pl.rename == []
    released = plan([placed(0, folder='Coast')], _many(20), confirm='mass_remove')
    assert released.rename == [('alb1', 'Trip', 'Coast')]


def test_execute_never_renames_a_folder_holding_a_subfolder(tmp_path):
    run(tmp_path, [placed(1)])
    (tmp_path / 'Trip/Nested').mkdir()  # e.g. another source's folder inside this one
    (tmp_path / 'Trip/Nested/theirs.jpg').write_bytes(b'x')
    pl, res = run(tmp_path, [placed(1, folder='Coast'), placed(2, folder='Coast')])
    assert pl.rename == [('alb1', 'Trip', 'Coast')]
    assert not (tmp_path / 'Coast').exists() and res.added == 1  # files still synced where it is
    assert photos(tmp_path / 'Trip') == ['2025-06-02_as001.jpg', '2025-06-03_as002.jpg', 'Nested']
    assert (tmp_path / 'Trip/Nested/theirs.jpg').read_bytes() == b'x'
    _, res = run(tmp_path, [], listed=5)  # everything removed: the folder stays for its subfolder
    assert res.removed == 2 and (tmp_path / 'Trip/Nested/theirs.jpg').exists()


def _progress_run(photo_dir, placed_list, fetch=_fetch):
    calls = []
    pl = lrs.plan(placed_list, lrs.read_owned(photo_dir, '', OWNER), len(placed_list), None)
    asyncio.run(lrs.execute(pl, photo_dir, OWNER, fetch, _direct, 0, timezone.utc, NOW,
                            progress=lambda *a: calls.append(a)))
    return calls


def test_execute_reports_progress(tmp_path):
    run(tmp_path, [placed(1)])
    calls = _progress_run(tmp_path, [placed(2), placed(3), placed(4)])
    assert calls == [('downloading', 0, 4), ('downloading', 1, 4), ('downloading', 2, 4),
                     ('downloading', 3, 4), ('removing', 3, 4), ('removing', 4, 4)]


def test_execute_progress_counts_skipped(tmp_path):
    async def one_missing(p):
        return None if p.photo.asset_id == 'as001' else JPEG

    calls = _progress_run(tmp_path, [placed(1), placed(2)], fetch=one_missing)
    assert calls[-1] == ('downloading', 2, 2)


def test_execute_progress_nothing_to_do(tmp_path):
    run(tmp_path, [placed(1)])
    assert _progress_run(tmp_path, [placed(1)]) == []


def _counting():
    calls = []

    async def fetch(p):
        calls.append(p.photo.asset_id)
        return JPEG
    return calls, fetch


def _keys(photo_dir, prefix=''):
    return {k: f.path for k, f in lrs.read_owned(photo_dir, prefix, OWNER).items()}


def test_read_owned_finds_nested_records(tmp_path):
    run(tmp_path, [placed(1, key='k1', folder='Trips/Alps'), placed(2, key='k2', folder='Zebra')])
    assert _keys(tmp_path) == {'k1': 'Trips/Alps', 'k2': 'Zebra'}


def test_read_owned_skips_hidden_and_at_folders(tmp_path):
    run(tmp_path, [placed(1, key='k1', folder='x'), placed(2, key='k2', folder='y')])
    (tmp_path / '@eaDir').mkdir()
    (tmp_path / '.hidden').mkdir()
    (tmp_path / 'x').rename(tmp_path / '@eaDir' / 'x')
    (tmp_path / 'y').rename(tmp_path / '.hidden' / 'y')
    assert _keys(tmp_path) == {}


def test_flat_folder_moves_into_the_tree(tmp_path):
    run(tmp_path, [placed(1, key='alb1', folder='Day 1')])
    calls, fetch = _counting()
    run(tmp_path, [placed(1, key='alb1', folder='2023/Iceland/Day 1')], fetch=fetch)
    assert photos(tmp_path / '2023/Iceland/Day 1') == ['2025-06-02_as001.jpg']
    assert marker(tmp_path / '2023/Iceland/Day 1')['path'] == '2023/Iceland/Day 1'
    assert not (tmp_path / 'Day 1').exists() and calls == []


def test_set_rename_moves_albums_and_prunes(tmp_path):
    run(tmp_path, [placed(1, key='k1', folder='Old/A'), placed(2, key='k2', folder='Old/B')])
    run(tmp_path, [placed(1, key='k1', folder='New/A'), placed(2, key='k2', folder='New/B')])
    assert _keys(tmp_path) == {'k1': 'New/A', 'k2': 'New/B'} and not (tmp_path / 'Old').exists()


def test_prune_keeps_a_folder_with_other_content(tmp_path):
    run(tmp_path, [placed(1, key='k1', folder='Old/A')])
    (tmp_path / 'Old' / 'notes.txt').write_text('mine')
    run(tmp_path, [placed(1, key='k1', folder='New/A')])
    assert sorted(p.name for p in (tmp_path / 'Old').iterdir()) == ['notes.txt']


def test_prune_stops_at_the_prefix(tmp_path):
    run(tmp_path, [placed(1, key='k1', folder='lr/A/B'), placed(2, key='k2', folder='lr/C')], prefix='lr')
    run(tmp_path, [placed(2, key='k2', folder='lr/C')], prefix='lr')
    assert not (tmp_path / 'lr' / 'A').exists() and (tmp_path / 'lr' / 'C').is_dir()


def test_prune_never_removes_the_prefix(tmp_path):
    run(tmp_path, [placed(1, key='k1', folder='lr/A')], prefix='lr')
    run(tmp_path, [placed(1, key='k1', folder='lr/B')], prefix='lr')
    run(tmp_path, [], prefix='lr', listed=1)
    assert (tmp_path / 'lr').is_dir()


def test_unrelated_empty_folder_is_left(tmp_path):
    (tmp_path / 'Empty').mkdir()
    run(tmp_path, [placed(1, key='k1', folder='Old/A'), placed(2, key='k2', folder='B')])
    run(tmp_path, [placed(1, key='k1', folder='New/A')])
    assert (tmp_path / 'Empty').is_dir()


def test_rename_ignores_eadir(tmp_path):
    run(tmp_path, [placed(1, key='k1', folder='A')])
    (tmp_path / 'A' / '@eaDir').mkdir()
    run(tmp_path, [placed(1, key='k1', folder='Set/A')])
    assert _keys(tmp_path) == {'k1': 'Set/A'} and (tmp_path / 'Set' / 'A' / '@eaDir').is_dir()


def test_move_waits_for_an_album_folder_in_the_way(tmp_path):
    # 'a-day' sorts first: its target lies inside the folder 'b-iceland' still occupies.
    run(tmp_path, [placed(1, key='b-iceland', folder='Iceland'), placed(2, key='a-day', folder='Day')])
    run(tmp_path, [placed(1, key='b-iceland', folder='2023/Iceland'), placed(2, key='a-day', folder='Iceland/Day')])
    assert _keys(tmp_path) == {'b-iceland': '2023/Iceland', 'a-day': 'Iceland/Day'}
    assert sorted(p.name for p in (tmp_path / 'Iceland').iterdir()) == ['Day']


def test_new_album_never_inside_an_album_folder(tmp_path):
    run(tmp_path, [placed(1, key='k1', folder='A')])
    run(tmp_path, [placed(1, key='k1', folder='A'), placed(2, key='k2', folder='A/B')])
    assert sorted(p.name for p in (tmp_path / 'A').iterdir() if p.is_dir()) == []


def test_ambiguous_nested_copy_is_untouched(tmp_path):
    run(tmp_path, [placed(1, key='k1', folder='A/X')])
    (tmp_path / 'B').mkdir()
    _copy(tmp_path / 'A' / 'X', tmp_path / 'B' / 'X')
    m = marker(tmp_path / 'B' / 'X')
    m['path'] = 'C/X'  # a copy that records neither place: two candidates, no self-recorded one
    (tmp_path / 'B' / 'X' / '.sync.json').write_text(json.dumps(m))
    (tmp_path / 'A' / 'X' / '.sync.json').write_text(json.dumps({**m, 'path': 'D/X'}))
    trees = {n: _tree(tmp_path / n / 'X') for n in ('A', 'B')}
    pl, _ = run(tmp_path, [placed(1, key='k1', folder='E/X')])
    assert pl.rename == [] and pl.ambiguous == [('k1', ['A/X', 'B/X'])]
    assert {n: _tree(tmp_path / n / 'X') for n in ('A', 'B')} == trees


def test_album_named_like_its_set_moves_into_it(tmp_path):
    run(tmp_path, [placed(1, key='k1', folder='Iceland'), placed(2, key='k2', folder='Day')])
    calls, fetch = _counting()
    run(tmp_path, [placed(1, key='k1', folder='Iceland/Iceland'), placed(2, key='k2', folder='Iceland/Day')], fetch=fetch)
    assert _keys(tmp_path) == {'k1': 'Iceland/Iceland', 'k2': 'Iceland/Day'} and calls == []


def test_month_folder_in_the_way_gives_the_set_a_suffix(tmp_path):
    run(tmp_path, [placed(1, key='2023-05', folder='2023-05')])
    calls, fetch = _counting()
    run(tmp_path, [placed(1, key='2023-05', folder='2023-05'), placed(2, key='k2', folder='2023-05/Day')], fetch=fetch)
    assert _keys(tmp_path) == {'2023-05': '2023-05', 'k2': '2023-05-2/Day'} and calls == ['as002']
    calls.clear()
    pl, _ = run(tmp_path, [placed(1, key='2023-05', folder='2023-05'), placed(2, key='k2', folder='2023-05/Day')], fetch=fetch)
    assert calls == [] and _keys(tmp_path) == {'2023-05': '2023-05', 'k2': '2023-05-2/Day'}


def test_flat_folder_blocked_by_a_month_folder_moves_beside_it(tmp_path):
    run(tmp_path, [placed(1, key='2023-05', folder='2023-05'), placed(2, key='k2', folder='Day')])
    run(tmp_path, [placed(1, key='2023-05', folder='2023-05'), placed(2, key='k2', folder='2023-05/Day')])
    assert _keys(tmp_path) == {'2023-05': '2023-05', 'k2': '2023-05-2/Day'}


def test_another_owners_folder_is_never_a_parent(tmp_path):
    (tmp_path / 'A').mkdir()
    (tmp_path / 'A' / '.sync.json').write_text(json.dumps({'owner': 'someone-else', 'key': 'x', 'items': {}}))
    run(tmp_path, [placed(2, key='k2', folder='A/B')])
    assert _keys(tmp_path) == {'k2': 'A-2/B'} and [p.name for p in (tmp_path / 'A').iterdir()] == ['.sync.json']


def test_a_file_where_a_set_folder_goes_gives_a_suffix(tmp_path):
    (tmp_path / '2023').write_text('a file, not a folder')
    run(tmp_path, [placed(1, key='k1', folder='2023/Day')])
    assert _keys(tmp_path) == {'k1': '2023-2/Day'} and (tmp_path / '2023').read_text() == 'a file, not a folder'
