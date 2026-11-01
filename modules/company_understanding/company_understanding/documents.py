"""Local extraction and evidence locations; never executes document content."""
import hashlib
import json
import re
import zipfile
from datetime import date
from pathlib import Path
from xml.etree import ElementTree

from bs4 import BeautifulSoup
from pypdf import PdfReader


def normalise(value):
    return re.sub(r'\s+', ' ', value).strip()


def source_path(folder, relative):
    path = (folder / relative).resolve()
    if not path.is_relative_to(folder.resolve()) or not path.is_file():
        raise ValueError('Source file is missing or outside the selected collection folder.')
    if path.suffix.lower() not in {'.pdf', '.html', '.htm', '.txt', '.csv', '.pptx', '.docx', '.xlsx', '.xls', '.ppt', '.doc', '.zip'}:
        raise ValueError('This file type is not a supported document input.')
    return path


def load_collection(folder):
    folder = Path(folder).expanduser().resolve()
    manifest = folder / 'catalogue.json'
    if not manifest.is_file() or not manifest.resolve().is_relative_to(folder):
        raise ValueError('Select a folder containing catalogue.json and its downloaded documents.')
    data = json.loads(manifest.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not isinstance(data.get('documents'), list):
        raise ValueError('The catalogue must contain a documents array.')
    if data.get('status') in {'running', 'needs_input'}:
        raise ValueError('Finish or stop the source collection before starting company understanding.')
    if data.get('demo'):
        raise ValueError('This is a synthetic collection. Use the separate Module 2 sample button instead.')
    if not any(d.get('file') for d in data['documents'] if isinstance(d, dict)):
        raise ValueError('The collection contains no saved documents.')
    return folder, data


def pdf_pages(path):
    reader = PdfReader(path)
    if reader.is_encrypted and not reader.decrypt(''):
        raise ValueError('Encrypted PDF could not be read.')
    for index, page in enumerate(reader.pages, 1):
        try:
            yield index, 'PDF page', page.extract_text(extraction_mode='layout') or '', None
        except Exception:
            yield index, 'PDF page', '', 'Text extraction failed on this page.'


def office_pages(path):
    with zipfile.ZipFile(path) as archive:
        if sum(x.file_size for x in archive.infolist()) > 100 * 1024 * 1024:
            raise ValueError('Office document expands beyond the 100 MB extraction safety limit.')
        if path.suffix.lower() == '.pptx':
            names = sorted((n for n in archive.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml', n)),
                           key=lambda n: int(re.search(r'(\d+)\.xml', n).group(1)))
            label = 'Slide'
        else:
            names, label = ['word/document.xml'], 'Document section'
        for index, name in enumerate(names, 1):
            xml = ElementTree.fromstring(archive.read(name))
            text = '\n'.join(' | '.join(' '.join(n.text or '' for n in cell.iter() if n.tag.endswith('}t')) for cell in row) for row in xml.iter() if row.tag.endswith('}tr'))
            paragraphs = [' '.join(n.text or '' for n in para.iter() if n.tag.endswith('}t')) for para in xml.iter() if para.tag.endswith('}p')]
            text = '\n'.join(paragraphs) + ('\nTABLE ROWS\n'+text if text else '')
            if not text.strip():
                text = ' '.join(n.text or '' for n in xml.iter() if n.tag.endswith('}t'))
            yield index, label, text, None


def extract(path, document_id):
    suffix = path.suffix.lower()
    if suffix == '.pdf':
        pages = pdf_pages(path)
    elif suffix in {'.pptx', '.docx'}:
        pages = office_pages(path)
    elif suffix in {'.html', '.htm'}:
        soup = BeautifulSoup(path.read_bytes(), 'html.parser')
        for node in soup.select('script, style, noscript, nav, header, footer, svg'):
            node.decompose()
        for table in soup.find_all('table'):
            rows = [' | '.join(cell.get_text(' ', strip=True) for cell in row.find_all(['th','td'])) for row in table.find_all('tr')]
            table.replace_with('\nTABLE\n' + '\n'.join(rows) + '\nEND TABLE\n')
        text = (soup.find('main') or soup.find('article') or soup).get_text('\n', strip=True)
        pages = [(1, 'HTML text block', text, None)]
    elif suffix in {'.txt', '.csv'}:
        pages = [(1, 'Text block', path.read_text(encoding='utf-8-sig', errors='replace'), None)]
    else:
        raise ValueError('Unsupported extraction format; convert this source to PDF, HTML, TXT, CSV, DOCX or PPTX.')
    chunks, issues, count = [], [], 0
    for number, label, text, error in pages:
        count += 1
        text = '\n'.join(line.rstrip() for line in text.splitlines()).strip()
        if error or len(text) < 30:
            issues.append(f'{label} {number}: {error or "Little or no extractable text; blank/image pages are not OCR-processed."}')
        if not text:
            continue
        # All text is covered. Overlap preserves sentences at chunk boundaries.
        for part, offset in enumerate(range(0, len(text), 7800), 1):
            value = text[offset:offset + 8000]
            chunks.append({'id': f'{document_id}-p{number}-c{part}', 'document_id': document_id,
                           'page': number, 'part': part, 'location': f'{label} {number}' + (f', part {part}' if len(text) > 8000 else ''),
                           'text': value, 'sha256': hashlib.sha256(value.encode()).hexdigest()})
    return chunks, issues, count


def publication_date(document):
    if document.get('date_status') == 'needs_review':
        return None
    try:
        return date.fromisoformat(document.get('published_date') or '')
    except ValueError:
        return None


def batches(items, max_chars=42000):
    group, size = [], 0
    for item in items:
        length = len(json.dumps(item, ensure_ascii=False))
        if group and size + length > max_chars:
            yield group
            group, size = [], 0
        group.append(item)
        size += length
    if group:
        yield group
