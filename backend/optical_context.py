"""Derived optical archive pages. Disabled until the runtime feasibility gate passes."""
import hashlib
import io
import json
from pathlib import Path


def select(records, requested, active=(), maximum=2):
    known = {r["id"]: r for r in records}
    chosen = []
    for ref in dict.fromkeys(requested):
        r = known.get(ref)
        # Missing classification is deliberately text-only. Trust is preserved as data.
        if r and ref not in active and r.get("optical_eligible") is True and r.get("priority") == "historical":
            chosen.append(r)
    return chosen[:maximum]


def render_record(record, directory, font_path, layout="readable", size=768):
    from PIL import Image, ImageDraw, ImageFont
    if layout not in ("readable", "dense", "structured") or size not in (512, 768, 1024):
        raise ValueError("Unsupported experimental layout or resolution")
    if record.get("optical_eligible") is not True or record.get("priority") != "historical":
        raise ValueError("Record is text-only")
    raw = record["text"]
    # V1 only renders a verified glyph range; all others explicitly use canonical text.
    if any(ord(c) > 126 or (ord(c) < 32 and c not in "\n\r\t") for c in raw):
        raise ValueError("Unsupported glyph; use canonical text")
    font_data = Path(font_path).read_bytes()
    font = ImageFont.truetype(io.BytesIO(font_data), 18 if layout == "readable" else 13)
    height = 24 if layout == "readable" else 17
    probe = ImageDraw.Draw(Image.new("RGB", (size, size)))
    lines = []
    for line in raw.expandtabs(4).split("\n"):
        while line and probe.textlength(line, font=font) > size-32:
            n = len(line)
            while probe.textlength(line[:n], font=font) > size-32:
                n -= 1
            lines.append(line[:n]);line=line[n:]
        lines.append(line)
    count = max(1, (size-72)//height)
    pages=[];directory=Path(directory);directory.mkdir(parents=True, exist_ok=True)
    for number, start in enumerate(range(0, len(lines), count), 1):
        image=Image.new("RGB", (size,size), "white");draw=ImageDraw.Draw(image)
        draw.text((16,8), f"{record['id']} page {number} ({layout}) DATA", font=font, fill="black")
        for i,line in enumerate(lines[start:start+count]):
            draw.text((16,48+i*height),line,font=font,fill="black")
        buffer=io.BytesIO();image.save(buffer,format="PNG",optimize=False);png=buffer.getvalue()
        digest=hashlib.sha256(png).hexdigest();file=directory/(digest+".png")
        file.write_bytes(png)
        metadata={"page_id": digest, "record_ids": [record["id"]], "layout": layout,
                  "width":size,"height":size,"render_version":1,"image_hash":digest,
                  "source_hash":hashlib.sha256(raw.encode()).hexdigest(),
                  "font_hash":hashlib.sha256(font_data).hexdigest(),"page_number":number,
                  "page_count":(len(lines)+count-1)//count,
                  "trust":record.get("trust","untrusted"),"path":str(file),
                  "visual_tokens":None}
        file.with_suffix(".json").write_text(json.dumps(metadata,indent=2),encoding="utf-8")
        pages.append(metadata)
    return pages


def representation(records, pages, enabled=False, capability_verified=False, max_pages=2):
    """No speculative image dispatch: unsupported/corrupt/overflow always retains text."""
    ids={r["id"] for r in records}
    known={r["id"]:r for r in records}
    usable=enabled and capability_verified and 0 < len(pages) <= max_pages
    covered=set()
    for page in pages:
        try:
            content=Path(page["path"]).read_bytes()
            usable=usable and hashlib.sha256(content).hexdigest()==page["image_hash"]
            covered.update(page["record_ids"])
            for ref in page["record_ids"]:
                record=known.get(ref,{})
                usable=usable and record.get("optical_eligible") is True and record.get("priority")=="historical"
                usable=usable and page["source_hash"]==hashlib.sha256(record.get("text","").encode()).hexdigest()
                group=[p for p in pages if ref in p.get("record_ids",[])]
                usable=usable and {p["page_number"] for p in group}==set(range(1,page["page_count"]+1))
        except (OSError,KeyError):
            usable=False
    usable=usable and covered==ids
    return {"mode":"optical" if usable else "text", "records":records,
            "pages":pages if usable else [], "fallback":not usable}
