"""tools/release_stamp.sh: the launcher starts only on a release the owner signed, checked offline."""
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / 'tools' / 'release_stamp.sh'

pytestmark = pytest.mark.skipif(not (shutil.which('git') and shutil.which('ssh-keygen')),
                                reason='needs git and ssh-keygen')


def git(cwd, *args, env=None):
    return subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


def tool(repo, *args):
    return subprocess.run(['bash', str(repo / 'tools' / 'release_stamp.sh'), *args], capture_output=True, text=True)


def keypair(path):
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'test', '-f', str(path)], check=True)
    return path.with_suffix('.pub').read_text().split()[:2]


def owner_repo(tmp_path, files):
    """The owner's checkout: the script, the published public key, `files`, and the private key."""
    owner = tmp_path / 'owner'
    git(tmp_path, 'init', '-q', '-b', 'main', str(owner))
    git(owner, 'config', 'user.email', 'owner@example.com')
    git(owner, 'config', 'user.name', 'owner')
    (owner / 'tools').mkdir()
    shutil.copy(SCRIPT, owner / 'tools' / 'release_stamp.sh')
    (owner / '.release-signing').mkdir(mode=0o700)
    kind, key = keypair(owner / '.release-signing' / 'release_signing_key')
    (owner / 'tools' / 'release_allowed_signers').write_text(
        f'dcdtuni-release namespaces="dcdtuni-release-stamp,dcdtuni-owner-checkout" {kind} {key}\n')
    (owner / '.gitignore').write_text('/.dcdtuni-owner-tree\n/.release-signing/\n/.release-stamp-sig.*\n')
    for name, text in files.items():
        (owner / name).parent.mkdir(parents=True, exist_ok=True)
        (owner / name).write_text(text)
    git(owner, 'add', '.')
    git(owner, 'commit', '-q', '-m', 'one')
    run = tool(owner, 'stamp')
    assert run.returncode == 0, run.stderr
    return owner


def clone(tmp_path, owner, name='mate', env=None):
    mate = tmp_path / name
    subprocess.run(['git', 'clone', '-q', str(owner), str(mate)], check=True, capture_output=True, env=env)
    git(mate, 'config', 'user.email', 'mate@example.com')
    git(mate, 'config', 'user.name', 'mate')
    return mate


@pytest.fixture
def release(tmp_path):
    owner = owner_repo(tmp_path, {'src/a.txt': 'one\n', 'case/Room/Room.cfdproj': '{}\n'})
    return owner, clone(tmp_path, owner)


def test_the_stamp_records_the_hash_the_date_and_the_content_commit(release):
    owner, _ = release
    text = (owner / 'RELEASE_STAMP').read_text()
    fields = dict(line.split('=', 1) for line in text.splitlines() if '=' in line and not line.startswith('#'))
    assert len(fields['tree_sha256']) == 64
    assert fields['stamped_at'].endswith(' SGT')
    assert fields['content_commit'] == git(owner, 'rev-parse', 'HEAD~1')
    assert (owner / 'RELEASE_STAMP.sig').read_text().startswith('-----BEGIN SSH SIGNATURE-----')


def test_a_clean_clone_of_the_signed_release_starts_without_a_network(release):
    _, mate = release
    git(mate, 'remote', 'remove', 'origin')  # nothing to reach
    (mate / 'scratch.log').write_text('untracked files are not checked\n')
    run = tool(mate, 'verify')
    assert run.returncode == 0, run.stderr
    assert '签名有效' in run.stdout
    assert '"status": "verified"' in tool(mate, 'status').stdout


def test_a_modified_tracked_file_refuses_to_start(release):
    _, mate = release
    (mate / 'src/a.txt').write_text('changed\n')
    run = tool(mate, 'verify')
    assert run.returncode == 1
    assert 'src/a.txt' in run.stderr and '拒绝启动' in run.stderr and '给代理' in run.stderr


def test_a_local_commit_that_changes_files_refuses_to_start(release):
    _, mate = release
    (mate / 'src/b.txt').write_text('mine\n')
    git(mate, 'add', 'src/b.txt')
    git(mate, 'commit', '-q', '-m', 'local')
    run = tool(mate, 'verify')
    assert run.returncode == 1
    assert '与发布印章不一致' in run.stderr


def test_restamping_without_the_owner_key_does_not_pass(release, tmp_path):
    """An agent that rewrites the hash after its change cannot sign it."""
    _, mate = release
    (mate / 'src/a.txt').write_text('changed\n')
    git(mate, 'commit', '-q', '-am', 'local')
    assert tool(mate, 'stamp').returncode == 1  # no private key in this checkout
    tree = subprocess.run(f"git -C {mate} ls-tree -r --full-tree HEAD -- src tools/release_stamp.sh "
                          f"tools/release_allowed_signers | sha256sum | cut -d' ' -f1",
                          shell=True, check=True, capture_output=True, text=True).stdout.strip()
    stamp = mate / 'RELEASE_STAMP'
    lines = [f'tree_sha256={tree}' if line.startswith('tree_sha256=') else line for line in stamp.read_text().splitlines()]
    stamp.write_text('\n'.join(lines) + '\n')
    keypair(tmp_path / 'forged')  # its own key: the published public key does not accept it
    subprocess.run(['ssh-keygen', '-q', '-Y', 'sign', '-f', str(tmp_path / 'forged'), '-n', 'dcdtuni-release-stamp', str(stamp)],
                   check=True)
    shutil.move(str(stamp) + '.sig', mate / 'RELEASE_STAMP.sig')
    git(mate, 'commit', '-q', '-am', 'restamp')
    run = tool(mate, 'verify')
    assert run.returncode == 1
    assert '签名无效' in run.stderr


def test_cases_and_other_data_outside_src_are_the_users_own(release):
    """A colleague edits a shipped case, adds a new one, and commits both: the release still starts."""
    _, mate = release
    (mate / 'case/Room/Room.cfdproj').write_text('{"edited": true}\n')
    (mate / 'case/Mine').mkdir()
    (mate / 'case/Mine/Mine.cfdproj').write_text('{}\n')
    (mate / 'notes.md').write_text('mine\n')
    run = tool(mate, 'verify')
    assert run.returncode == 0, run.stderr
    git(mate, 'add', '.')
    git(mate, 'commit', '-q', '-m', 'my cases')
    run = tool(mate, 'verify')
    assert run.returncode == 0, run.stderr


def test_a_self_made_owner_marker_refuses_to_start(release):
    _, mate = release
    (mate / '.dcdtuni-owner-tree').write_text('')
    run = tool(mate, 'verify')
    assert run.returncode == 1 and '不是所有者签名的标记' in run.stderr


def test_the_owner_marker_is_signed_and_portable(release, tmp_path):
    owner, mate = release
    assert tool(owner, 'owner-marker').returncode == 0
    (owner / 'src/a.txt').write_text('work in progress\n')
    run = tool(owner, 'verify')
    assert run.returncode == 0 and '跳过' in run.stdout
    other = clone(tmp_path, owner, 'owner-elsewhere')  # another owner machine: copy the marker
    shutil.copy(owner / '.dcdtuni-owner-tree', other / '.dcdtuni-owner-tree')
    (other / 'src/a.txt').write_text('also in progress\n')
    assert tool(other, 'verify').returncode == 0


def test_an_lfs_file_with_only_its_pointer_on_disk_refuses_to_start(tmp_path):
    """No git-lfs, or objects never fetched: status is clean, the texture is missing."""
    texture = b'\x89PNG texture bytes\n'
    pointer = ('version https://git-lfs.github.com/spec/v1\n'
               f'oid sha256:{hashlib.sha256(texture).hexdigest()}\nsize {len(texture)}\n')
    owner = owner_repo(tmp_path, {'.gitattributes': '*.png filter=lfs diff=lfs merge=lfs -text\n',
                                  'src/wall.png': pointer, 'case/floor.png': pointer})  # the blob git stores for an LFS file
    mate = clone(tmp_path, owner, env={**os.environ, 'GIT_LFS_SKIP_SMUDGE': '1'})
    run = tool(mate, 'verify')
    assert run.returncode == 1
    assert '只有指针' in run.stderr and 'src/wall.png' in run.stderr and 'git lfs pull' in run.stderr
    assert 'case/floor.png' not in run.stderr  # outside the stamp's scope
    (mate / 'src/wall.png').write_bytes(b'\x89PNG other bytes of the same.\n'[:len(texture)])
    run = tool(mate, 'verify')
    assert run.returncode == 1 and 'wall.png' in run.stderr


def test_the_pre_push_check_takes_a_commit(release):
    owner, _ = release
    assert tool(owner, 'verify', '--commit', 'HEAD').returncode == 0
    (owner / 'src/a.txt').write_text('two\n')
    git(owner, 'commit', '-q', '-am', 'two')
    assert tool(owner, 'verify', '--commit', 'HEAD').returncode == 1  # not stamped yet
    assert tool(owner, 'stamp').returncode == 0
    assert tool(owner, 'verify', '--commit', 'HEAD').returncode == 0


def test_inbox_check_flags_changes_to_the_check(release):
    owner, mate = release
    for name, text in {'src/start_server.sh': '"$REPO_ROOT/tools/release_stamp.sh" verify >"$LOG" &\n'
                                              'banner() {\n    await_release_check\n}\n',
                       'src/server/server.py': "if __name__ == '__main__':\n    release_check.verify_or_exit()\n"}.items():
        (owner / name).parent.mkdir(parents=True, exist_ok=True)
        (owner / name).write_text(text)
    git(owner, 'add', '.')
    git(owner, 'commit', '-q', '-m', 'launcher')
    git(owner, 'branch', 'harmless')
    git(owner, 'checkout', '-q', '-b', 'feature')
    (owner / 'b.txt').write_text('feature\n')
    git(owner, 'add', 'b.txt')
    git(owner, 'commit', '-q', '-m', 'feature')
    assert tool(owner, 'inbox-check', 'harmless..feature').returncode == 0
    git(owner, 'checkout', '-q', '-b', 'sneaky')
    (owner / 'src/server/server.py').write_text("if __name__ == '__main__':\n    pass\n")
    git(owner, 'commit', '-q', '-am', 'faster start')
    run = tool(owner, 'inbox-check', 'harmless..sneaky')
    assert run.returncode == 1 and 'release_check.verify_or_exit()' in run.stderr
    git(owner, 'checkout', '-q', '-b', 'stamper', 'feature')
    (owner / 'tools' / 'release_stamp.sh').write_text('#!/bin/sh\nexit 0\n')
    git(owner, 'commit', '-q', '-am', 'simplify')
    git(owner, 'checkout', '-q', 'feature')  # the owner checks from the owner's own script
    run = tool(owner, 'inbox-check', 'harmless..stamper')
    assert run.returncode == 1 and 'tools/release_stamp.sh' in run.stderr
