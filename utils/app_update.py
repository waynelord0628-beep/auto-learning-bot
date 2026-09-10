"""Release selection, verified download, and an isolated replacement helper."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

REPO = 'waynelord0628-beep/auto-learning-bot'
API = 'https://api.github.com/repos/' + REPO + '/releases/latest'


def release_offer(data, current):
    def version(s):
        match = re.fullmatch(r'[vV]?(\d+)\.(\d+)\.(\d+)', str(s).strip())
        return tuple(map(int, match.groups())) if match else None
    latest = version(data.get('tag_name'))
    if data.get('draft') or data.get('prerelease') or not latest or not version(current) or latest <= version(current):
        return None
    assets = [a for a in data.get('assets', []) if a.get('name', '').lower().endswith('.exe') and a.get('state') == 'uploaded']
    if len(assets) != 1:
        return None
    asset = assets[0]
    digest = asset.get('digest', '')
    url = asset.get('browser_download_url', '')
    if not re.fullmatch(r'sha256:[a-fA-F0-9]{64}', digest) or not isinstance(asset.get('size'), int) or asset['size'] <= 0:
        return None
    if not url.startswith('https://github.com/' + REPO + '/releases/download/' + data['tag_name'] + '/'):
        return None
    return {'version': 'V' + '.'.join(map(str, latest)), 'notes': str(data.get('body') or ''), 'url': url, 'size': asset['size'], 'sha256': digest[7:].lower()}


def verify_file(path, size, digest):
    path = Path(path)
    if path.stat().st_size != size:
        raise ValueError('更新檔大小不符')
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError('更新檔不是 EXE')
        stream.seek(0)
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(chunk)
    if sha.hexdigest() != digest:
        raise ValueError('更新檔驗證失敗')


def download(offer, stage, get, cancelled, progress):
    stage = Path(stage)
    partial = stage / 'download.part'
    complete = stage / 'incoming.exe'
    received = 0
    try:
        with get(offer['url'], stream=True, timeout=(10, 30)) as response:
            response.raise_for_status()
            if not response.url.startswith('https://'):
                raise ValueError('更新下載來源不安全')
            with partial.open('wb') as stream:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if cancelled():
                        raise ValueError('已取消更新')
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > offer['size']:
                        raise ValueError('更新檔超過預期大小')
                    stream.write(chunk)
                    progress(received, offer['size'])
                stream.flush()
                os.fsync(stream.fileno())
        if cancelled():
            raise ValueError('已取消更新')
        verify_file(partial, offer['size'], offer['sha256'])
        os.replace(partial, complete)
        return str(complete)
    finally:
        if partial.exists():
            partial.unlink()


def write_state(path, state):
    from utils.config_store import atomic_write_json
    atomic_write_json(path, state)


def prepare_update(target, incoming, offer, pid, created):
    target, incoming = Path(target).resolve(), Path(incoming).resolve()
    stage = incoming.parent
    if stage.parent != target.parent / '.updates' or incoming.name != 'incoming.exe':
        raise ValueError('更新暫存位置不符')
    verify_file(incoming, offer['size'], offer['sha256'])
    helper = stage / 'updater.exe'
    shutil.copy2(target, helper)
    manifest = stage / 'transaction.json'
    state = {'target': str(target), 'stage': str(stage), 'version': offer['version'], 'size': offer['size'],
             'sha256': offer['sha256'], 'pid': pid, 'created': created, 'state': 'prepared'}
    write_state(manifest, state)
    return helper, manifest


def replace_with_backup(target, incoming, backup):
    """Retain the old executable even if installing the new one fails."""
    target, incoming, backup = map(Path, (target, incoming, backup))
    if backup.exists():
        raise ValueError('更新備份已存在')
    os.replace(target, backup)
    try:
        os.replace(incoming, target)
    except Exception:
        os.replace(backup, target)
        raise


def helper_main(manifest_path):
    import psutil
    manifest = Path(manifest_path).resolve()
    state = json.loads(manifest.read_text(encoding='utf-8'))
    stage = manifest.parent
    target = Path(state['target']).resolve()
    if stage != Path(state['stage']).resolve() or stage.parent != target.parent / '.updates' or target.suffix.lower() != '.exe':
        raise ValueError('Invalid update transaction')
    incoming, backup = stage / 'incoming.exe', stage / 'previous.exe'
    installed = False
    def save(status):
        state['state'] = status
        write_state(manifest, state)
    try:
        original = psutil.Process(state['pid'])
        if abs(original.create_time() - state['created']) < .01:
            original.wait(timeout=60)
    except psutil.NoSuchProcess:
        pass
    except psutil.TimeoutExpired:
        save('old_process_not_stopped')
        return 1
    try:
        verify_file(incoming, state['size'], state['sha256'])
        # The one-file bootloader may briefly retain its handle after the UI exits.
        for attempt in range(30):
            try:
                replace_with_backup(target, incoming, backup)
                break
            except PermissionError:
                if attempt == 29:
                    raise
                time.sleep(1)
        installed = True
        save('installed_pending_startup')
        env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1')
        child = subprocess.Popen([str(target), '--update-ready', str(manifest)], cwd=target.parent, env=env)
        for _ in range(120):
            if (stage / 'ready.json').exists():
                ready = json.loads((stage / 'ready.json').read_text(encoding='utf-8'))
                if ready.get('version') == state['version'] and ready.get('target') == str(target):
                    save('complete')
                    return 0
            if child.poll() is not None:
                break
            time.sleep(1)
        try:
            process = psutil.Process(child.pid)
            family = process.children(recursive=True) + [process]
            for member in family:
                member.kill()
            psutil.wait_procs(family, timeout=10)
        except psutil.NoSuchProcess:
            pass
        os.replace(target, stage / 'failed.exe')
        os.replace(backup, target)
        save('rolled_back')
        subprocess.Popen([str(target), '--update-rollback'], cwd=target.parent, env=env)
        return 1
    except Exception as error:
        # Launch errors must restore the old executable too, not only handshake timeouts.
        if installed and backup.exists():
            if target.exists():
                os.replace(target, stage / 'failed.exe')
            os.replace(backup, target)
            state['error'] = type(error).__name__
            save('rolled_back')
            subprocess.Popen([str(target), '--update-rollback'], cwd=target.parent,
                             env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1'))
            return 1
        if backup.exists() and not target.exists():
            os.replace(backup, target)
        state['error'] = type(error).__name__
        save('failed')
        return 1


def acknowledge_startup(manifest_path, version):
    manifest = Path(manifest_path).resolve()
    state = json.loads(manifest.read_text(encoding='utf-8'))
    target = Path(sys.executable).resolve()
    if state['version'] != version or Path(state['target']).resolve() != target or manifest.parent.parent != target.parent / '.updates':
        raise ValueError('Startup acknowledgement mismatch')
    write_state(manifest.parent / 'ready.json', {'version': version, 'target': str(target)})
