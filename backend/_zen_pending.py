"""Apply a verified staged source update before imports, only when the old backend is absent."""
import hashlib
import json
import os
import socket
from contextlib import contextmanager
from pathlib import Path

ALLOWED = frozenset(('agentic.py', 'agent_tools.py', 'agent_config.py',
                     'batch_execution.py', 'batch_store.py', 'batch_tools.py', 'context_checkpoint.py', 'optical_context.py'))


def digest(data): return hashlib.sha256(data).hexdigest()


@contextmanager
def startup_lock(staged):
    # OS-owned locks are released on crashes; no stale PID or lock-directory recovery is needed.
    with (staged/'apply.lock').open('a+b') as file:
        file.seek(0, 2)
        if file.tell() == 0:file.write(b'0');file.flush()
        file.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(file, fcntl.LOCK_EX)
        try: yield
        finally:
            file.seek(0)
            if os.name == 'nt':msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:fcntl.flock(file, fcntl.LOCK_UN)


def apply_pending(directory):
    root = Path(directory).resolve()
    staged = root/'.pending_updates'/'bte-v1'
    if not staged.resolve().is_relative_to(root):raise ValueError('Staging directory leaves the app')
    manifest = staged/'manifest.json'
    if not manifest.exists(): return False
    doc = json.loads(manifest.read_text(encoding='utf-8'))
    port = doc.get('runtime_port', 8765)
    if not isinstance(port, int) or not 1 <= port <= 65535: raise ValueError('Invalid update port')
    with socket.socket() as sock:
        sock.settimeout(.2)
        if sock.connect_ex(('127.0.0.1', port)) == 0:
            return False  # reopening a window must never change an active resident backend
    with startup_lock(staged):
        if not manifest.exists():return False
        return apply_verified(root, staged, manifest)


def apply_verified(root, staged, manifest):
    doc = json.loads(manifest.read_text(encoding='utf-8'))
    prepared=[];originals={}
    names = [entry['name'] for entry in doc['files']]
    if len(names) != len(set(names)):raise ValueError('Duplicate update target')
    for entry in doc['files']:
        name=entry['name']
        if name not in ALLOWED: raise ValueError('Unsupported update target')
        path=root/name;payload=staged/'payload'/name
        if (path.resolve().parent != root or path.is_symlink() or payload.is_symlink() or
                not payload.resolve().is_relative_to(staged.resolve()) or payload.resolve().parent != (staged/'payload').resolve()):
            raise ValueError('Update paths must stay inside the app')
        current=path.read_bytes() if path.exists() else None
        if (digest(current) if current is not None else None) != entry['expected_sha256']:
            (staged/'conflict.json').write_text(json.dumps({'file':name,'reason':'live file changed since staging'}))
            return False  # preserve local changes; start the old app normally
        data=payload.read_bytes()
        if digest(data) != entry['payload_sha256']: raise ValueError('Staged payload hash mismatch')
        compile(data.decode('utf-8'),str(path),'exec')
        originals[name]=current
        prepared.append((path,data))
    backup=staged/'backup';backup.mkdir(exist_ok=True)
    for name,data in originals.items():
        if data is not None:(backup/name).write_bytes(data)
    temporary=[];applied=[]
    try:
        for path,data in prepared:
            temp=path.with_suffix('.py.bte-new');temp.write_bytes(data);temporary.append((temp,path))
        for temp,path in temporary:
            os.replace(temp,path);applied.append(path)
    except Exception:
        for path in reversed(applied):
            data=originals[path.name]
            if data is None:path.unlink()
            else:
                temp=path.with_suffix('.py.bte-rollback');temp.write_bytes(data);os.replace(temp,path)
        raise
    finally:
        for temp,_ in temporary:
            if temp.exists():temp.unlink()
    manifest.replace(staged/'applied.json')
    return True
