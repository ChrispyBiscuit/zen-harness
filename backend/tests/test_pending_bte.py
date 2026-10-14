import json
import socket
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _zen_pending as update


def stage(tmp_path, port):
    folder=tmp_path/'.pending_updates'/'bte-v1';(folder/'payload').mkdir(parents=True)
    old=b'answer = 0\n';new=b'answer = 1\n'
    (tmp_path/'agentic.py').write_bytes(old);(folder/'payload'/'agentic.py').write_bytes(new)
    doc={'runtime_port':port,'files':[{'name':'agentic.py','expected_sha256':update.digest(old),
                                       'payload_sha256':update.digest(new)}]}
    (folder/'manifest.json').write_text(json.dumps(doc))
    return folder,old,new


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def test_active_backend_blocks_activation_without_touching_files(tmp_path):
    with socket.socket() as server:
        server.bind(('127.0.0.1',0));server.listen()
        folder,old,new=stage(tmp_path,server.getsockname()[1])
        assert update.apply_pending(tmp_path) is False
        assert (tmp_path/'agentic.py').read_bytes()==old and (folder/'manifest.json').exists()


def test_next_backend_start_applies_once_and_keeps_backup(tmp_path):
    folder,old,new=stage(tmp_path,free_port())
    assert update.apply_pending(tmp_path) is True
    assert (tmp_path/'agentic.py').read_bytes()==new
    assert (folder/'backup'/'agentic.py').read_bytes()==old
    assert (folder/'applied.json').exists() and not (folder/'manifest.json').exists()
    assert update.apply_pending(tmp_path) is False


def test_conflicting_local_changes_are_preserved(tmp_path):
    folder,old,new=stage(tmp_path,free_port())
    (tmp_path/'agentic.py').write_text('local change\n')
    assert update.apply_pending(tmp_path) is False
    assert (tmp_path/'agentic.py').read_text()=='local change\n'
    assert (folder/'conflict.json').exists()


def test_bad_payload_hash_or_syntax_never_changes_runtime(tmp_path):
    folder,old,new=stage(tmp_path,free_port())
    (folder/'payload'/'agentic.py').write_text('different\n')
    with pytest.raises(ValueError):update.apply_pending(tmp_path)
    assert (tmp_path/'agentic.py').read_bytes()==old


def test_failed_transaction_rolls_back_before_imports(tmp_path,monkeypatch):
    folder,old,new=stage(tmp_path,free_port())
    (tmp_path/'agent_tools.py').write_bytes(b'value = 0\n')
    data=b'value = 1\n';(folder/'payload'/'agent_tools.py').write_bytes(data)
    doc=json.loads((folder/'manifest.json').read_text())
    doc['files'].append({'name':'agent_tools.py','expected_sha256':update.digest(b'value = 0\n'),
                         'payload_sha256':update.digest(data)})
    (folder/'manifest.json').write_text(json.dumps(doc))
    real=update.os.replace;failed=[]
    def replace(src,dst):
        if str(src).endswith('agent_tools.py.bte-new') and not failed:
            failed.append(True);raise OSError('simulated commit failure')
        return real(src,dst)
    monkeypatch.setattr(update.os,'replace',replace)
    with pytest.raises(OSError):update.apply_pending(tmp_path)
    assert (tmp_path/'agentic.py').read_bytes()==old
    assert (tmp_path/'agent_tools.py').read_text()=='value = 0\n'
    assert (folder/'manifest.json').exists()
