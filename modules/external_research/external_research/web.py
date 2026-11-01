"""Public-source retrieval. Only search-observed URLs are passed by the engine."""
import asyncio
import hashlib
import io
import ipaddress
import socket
from urllib.parse import urlsplit, urljoin, urlunsplit

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

async def public_url(url):
    parsed=urlsplit(url)
    if parsed.scheme not in {'https','http'} or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None,80,443}:
        raise ValueError('Only public HTTP(S) source URLs are supported.')
    addresses=await asyncio.to_thread(socket.getaddrinfo,parsed.hostname,parsed.port or (443 if parsed.scheme=='https' else 80),0,socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('Private or local network sources are blocked.')
    address=addresses[0][4][0]
    host=f'[{address}]' if ':' in address else address
    if parsed.port:host+=f':{parsed.port}'
    return urlunsplit((parsed.scheme,host,parsed.path,parsed.query,'')),parsed.netloc,parsed.hostname


def parse_content(body, content_type):
    if body.startswith(b'%PDF-'):
        reader=PdfReader(io.BytesIO(body))
        if reader.is_encrypted and not reader.decrypt(''):
            raise ValueError('Encrypted source PDF.')
        parts=[]
        for i,p in enumerate(reader.pages,1):
            parts.append({'location':f'PDF page {i}','text':p.extract_text(extraction_mode='layout') or ''})
        return parts, []
    if 'html' not in content_type and not body.lstrip().startswith((b'<!',b'<html',b'<HTML')):
        raise ValueError('Source is not a supported HTML or PDF document.')
    soup=BeautifulSoup(body,'html.parser')
    dates=[]
    for node in soup.select('meta[property="article:published_time"], meta[name="date"], meta[name="DC.date.issued"], time[datetime]'):
        value=node.get('content') or node.get('datetime')
        if value:
            dates.append(value)
    for node in soup.select('script,style,noscript,nav,svg,footer'):
        node.decompose()
    for table in soup.find_all('table'):
        table.replace_with('\n'+'\n'.join(' | '.join(c.get_text(' ',strip=True) for c in row.find_all(['th','td'])) for row in table.find_all('tr'))+'\n')
    text=(soup.find('main') or soup.find('article') or soup).get_text('\n',strip=True)
    # Metadata is labelled explicitly; a time element may describe an event, not publication.
    return [{'location':'HTML page','text':text}], dates

class Fetcher:
    async def fetch(self,url):
        async with httpx.AsyncClient(timeout=30,follow_redirects=False,trust_env=False,headers={'User-Agent':UA}) as client:
            for _ in range(6):
                target,host,hostname=await public_url(url)
                # Pin the validated address while retaining TLS certificate/SNI checks.
                async with client.stream('GET',target,headers={'Host':host},extensions={'sni_hostname':hostname}) as response:
                    if response.is_redirect:
                        url=urljoin(url,response.headers['location'])
                        continue
                    response.raise_for_status()
                    body=bytearray()
                    async for piece in response.aiter_bytes():
                        body.extend(piece)
                        if len(body)>25*1024*1024:
                            raise ValueError('Source exceeds the 25 MB per-file safety limit.')
                    parts,dates=await asyncio.to_thread(parse_content,bytes(body),response.headers.get('content-type',''))
                    if not any(p['text'].strip() for p in parts):
                        raise ValueError('No readable text; scanned PDFs require OCR.')
                    return {'url':url,'parts':parts,'date_metadata':dates,'sha256':hashlib.sha256(body).hexdigest(), 'body':bytes(body),
                            'extension':'.pdf' if body.startswith(b'%PDF-') else '.html'}
        raise ValueError('Too many source redirects.')
