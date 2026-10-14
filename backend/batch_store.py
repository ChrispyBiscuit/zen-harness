"""Per-chat, atomic batch-result storage with bounded retention and paged retrieval."""
import base64
import json
import re
import threading
from pathlib import Path

LOCK = threading.RLock()


class BatchStore:
    def __init__(self, root, config):
        self.root = Path(root)
        self.config = config

    def _path(self, batch_id):
        if not re.fullmatch(r'b_[0-9a-f]{32}', batch_id): raise ValueError('Invalid batch ID')
        return self.root/(batch_id+'.json')

    def put(self, result):
        if not self.config['preserve_full_outputs']: return
        doc = {k: v for k, v in result.items() if k != 'operations'}
        doc['operations'] = []
        for r in result['operations']:
            row = {k: v for k, v in r.items() if not k.startswith('_')}
            image = r.get('_image')
            if image: row['image_base64'] = base64.b64encode(image).decode('ascii')
            doc['operations'].append(row)
        encoded = json.dumps(doc, ensure_ascii=False).encode('utf-8')
        if len(encoded) > self.config['retention_bytes']:
            raise ValueError('Raw batch output exceeds configured storage limit and cannot be retained')
        with LOCK:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self._path(result['batch_id'])
            temp = path.with_suffix('.tmp'); temp.write_bytes(encoded); temp.replace(path)
            used = 0
            files = sorted(self.root.glob('b_*.json'), key=lambda p: p.stat().st_mtime_ns, reverse=True)
            for i, p in enumerate(files):
                used += p.stat().st_size
                if i >= self.config['retention_batches'] or used > self.config['retention_bytes']:
                    p.unlink()  # only our validated flat result files, never recursive directory removal

    def get(self, batch_id, operation_id, offset=0, limit=8000):
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError('offset must be a nonnegative integer')
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 8000:
            raise ValueError('limit must be 1–8000 characters')
        path = self._path(batch_id)
        if not path.exists(): raise ValueError('Batch result not found or retention expired')
        doc = json.loads(path.read_text(encoding='utf-8'))
        row = next((r for r in doc['operations'] if r['id'] == operation_id), None)
        if row is None: raise ValueError('Unknown operation ID')
        raw = str(row.get('output', ''))
        end = min(len(raw), offset+limit)
        head = (f"{batch_id}/{operation_id} {row['status']} tool={row['tool']} "
                f"depends_on={json.dumps(row.get('depends_on', []))} condition={row.get('condition', 'always')} "
                f"characters {offset}:{end}/{len(raw)}\n")
        tail = f'\n[truncated; next offset={end}]' if end < len(raw) else ''
        image = base64.b64decode(row['image_base64']) if row.get('image_base64') and offset == 0 else None
        return head+raw[offset:end]+tail, image
