#!/usr/bin/env python3
"""External content sources: Wattpad, Quora. All fail silently."""

import html
import logging
import re
import time
from typing import Callable, Dict, List, Optional, Set

import requests

logger = logging.getLogger(__name__)

_BROWSER_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
               '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
_TAG_RE = re.compile(r'<[^>]+>')


def _session(user_agent: Optional[str]) -> requests.Session:
    s = requests.Session()
    s.headers.update({'User-Agent': user_agent or _BROWSER_UA})
    return s


def _get_with_retry(session: requests.Session, url: str, *, timeout: int = 15, **kw) -> requests.Response:
    for attempt in range(3):
        try:
            r = session.get(url, timeout=timeout, **kw)
            r.raise_for_status()
            return r
        except requests.HTTPError as e:
            st = e.response.status_code if e.response is not None else None
            if (st == 429 or (st and st >= 500)) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise
        except requests.RequestException:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise


def _ascii_ratio(text: str) -> float:
    return sum(c.isascii() for c in text) / max(len(text), 1)


def _strip_html(raw: str) -> str:
    txt = re.sub(r'<br\s*/?>', '\n', raw or '', flags=re.IGNORECASE)
    return html.unescape(_TAG_RE.sub('', txt)).strip()


def _make_post(pid: str, title: str, text: str, score, source: str, subreddit: str, words: int) -> Dict:
    return {'id': pid, 'title': title, 'text': text, 'score': int(score or 0),
            'author': source, 'subreddit': subreddit, 'source': source,
            'over_18': False, 'words': words}


def _accept(blob: str, body: str, wc: int, min_w: int, max_w: int,
            is_safe: Callable[[str], bool]) -> bool:
    return min_w <= wc <= max_w and is_safe(blob) and _ascii_ratio(body) >= 0.8


# ── Wattpad ───────────────────────────────────────────────────────────────

def _wattpad_text(session: requests.Session, story_id) -> str:
    try:
        data  = _get_with_retry(session, f'https://www.wattpad.com/api/v3/stories/{story_id}/parts',
                                params={'fields': 'id,text'}, timeout=12).json()
    except Exception:
        return ''
    parts  = data if isinstance(data, list) else data.get('parts', [])
    chunks = []
    for part in parts[:3]:
        txt = part.get('text') or ''
        if txt.startswith('http'):
            try:
                txt = _get_with_retry(session, txt, timeout=12).text
            except Exception:
                txt = ''
        chunks.append(_strip_html(txt))
    return '\n'.join(c for c in chunks if c).strip()


def fetch_wattpad(query: str, limit: int, seen_ids: Optional[Set[str]] = None,
                  user_agent: Optional[str] = None,
                  is_safe: Optional[Callable[[str], bool]] = None,
                  min_words: int = 200, max_words: int = 800) -> List[Dict]:
    is_safe = is_safe or (lambda _: True)
    seen    = seen_ids or set()
    session = _session(user_agent)
    try:
        payload = _get_with_retry(
            session, 'https://www.wattpad.com/api/v3/stories/search',
            params={'query': query, 'limit': 20,
                    'fields': 'stories(id,title,description,numParts,mature,language(id),readCount)'},
            timeout=12).json()
    except Exception as e:
        logger.warning(f'Wattpad unavailable: {type(e).__name__}')
        return []
    stories = payload.get('stories', payload) if isinstance(payload, dict) else payload
    out: List[Dict] = []
    for st in stories or []:
        sid = st.get('id')
        pid = f'wattpad_{sid}'
        if not sid or pid in seen:
            continue
        if st.get('mature') or (st.get('language') or {}).get('id', 1) != 1:
            continue
        if (st.get('numParts') or 99) > 3:
            continue
        title = (st.get('title') or '').strip()
        text  = _wattpad_text(session, sid)
        if not text:
            continue
        wc = len((title + ' ' + text).split())
        if _accept(title + ' ' + text, text, wc, min_words, max_words, is_safe):
            out.append(_make_post(pid, title, text, st.get('readCount', 0), 'wattpad', 'wattpad', wc))
            if len(out) >= limit:
                break
    logger.info(f'Wattpad "{query}": {len(out)} stories')
    return out


# ── Quora ─────────────────────────────────────────────────────────────────

def fetch_quora(topic_slug: str, limit: int, seen_ids: Optional[Set[str]] = None,
                user_agent: Optional[str] = None,
                is_safe: Optional[Callable[[str], bool]] = None,
                min_words: int = 150, max_words: int = 800) -> List[Dict]:
    is_safe = is_safe or (lambda _: True)
    seen    = seen_ids or set()
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return []
    session = _session(user_agent)
    try:
        r    = _get_with_retry(session, f'https://www.quora.com/topic/{topic_slug}', timeout=12)
        soup = BeautifulSoup(r.text, 'html.parser')
    except Exception as e:
        logger.warning(f'Quora unavailable: {type(e).__name__}')
        return []
    out: List[Dict] = []
    for i, block in enumerate(soup.select('.q-text')):
        text = block.get_text(' ', strip=True)
        pid  = f'quora_{topic_slug}_{i}'
        if not text or pid in seen:
            continue
        wc = len(text.split())
        if _accept(text, text, wc, min_words, max_words, is_safe):
            out.append(_make_post(pid, text[:80].strip(), text, 0, 'quora', 'quora', wc))
            if len(out) >= limit:
                break
    logger.info(f'Quora "{topic_slug}": {len(out)} answers')
    return out
