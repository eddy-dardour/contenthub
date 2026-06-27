#!/usr/bin/env python3
"""
Scraper — Reddit (backends publics sans auth) + sources externes.
Stratégies : story (AmItheAsshole…) | facts (todayilearned…)
"""

import html
import logging
import math
import random
import re
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional, Set

from censor import censor
from external_providers import fetch_quora, fetch_wattpad
from reddit_providers import RedditProviderChain

logger = logging.getLogger(__name__)

FACTS_BATCH_WORDS = 220
FACTS_MIN_COUNT   = 8
FACTS_MAX_COUNT   = 15
STORY_MAX_WORDS   = 800
STORY_MIN_WORDS   = 200
STORY_MIN_SCORE   = 800
STORY_TIME_FILTER = 'month'
STORY_OPTIMAL_MIN = 200
STORY_OPTIMAL_MAX = 600
POOL_BATCH        = 10   # fetch parallèle de subreddits (I/O-bound : large = rapide)

ADAPTIVE_WIDENING = [
    {'time_filter': 'year', 'min_score': 200},
    {'time_filter': 'all',  'min_score': 50},
]

_SEXUAL_TERMS = [
    'sex','sexual','sexually','porn','porno','pornography','nude','nudes',
    'naked','orgasm','masturbate','masturbated','masturbation','masturbating',
    'horny','aroused','erection','erect','penis','vagina','genitals',
    'genitalia','boobs','breasts','nipple','nipples','blowjob','handjob',
    'cum','cumming','ejaculate','ejaculation','intercourse','foreplay',
    'kinky','fetish','bdsm','nsfw','incest','rape','raped','molest',
    'molested','molestation','pedophile','pedophilia','fondle','fondled',
    'grope','groped','thrust','thrusting','erotic','erotica','seduce',
    'seduced','seductive','foursome','threesome','hookup',
]
_SEXUAL_RE = re.compile(
    r'\b(' + '|'.join(re.escape(t) for t in sorted(set(_SEXUAL_TERMS), key=len, reverse=True)) + r')\b',
    re.IGNORECASE,
)

def _is_safe(text: str) -> bool:
    # Only hard-reject sexual content — sensitive words are replaced by censor()
    return not _SEXUAL_RE.search(text or '')

def title_fingerprint(title: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', (title or '').lower())[:80]

_URL_RE              = re.compile(r'https?://\S+|www\.\S+')
_MD_LINK_RE          = re.compile(r'\[([^\]]+)\]\([^)]+\)')
_SPOILER_RE          = re.compile(r'>!\s*(.+?)\s*!<', re.DOTALL)
_REDDIT_REF_RE       = re.compile(r'\b([ru])/([A-Za-z0-9_]+)', re.IGNORECASE)
_READ_ALOUD_RE       = re.compile(r'[*~`#^|\\=<>{}\[\]]+')

def clean_tts_text(text: str) -> str:
    if not text:
        return ''
    t = html.unescape(text).replace('​', '')
    t = _MD_LINK_RE.sub(r'\1', t)
    t = _SPOILER_RE.sub(r'\1', t)
    t = _URL_RE.sub(' ', t)
    t = _REDDIT_REF_RE.sub(r'\2', t)
    t = t.replace('&', ' and ').replace('/', ' ').replace('_', ' ')
    t = _READ_ALOUD_RE.sub(' ', t)
    t = re.sub(r'\s[-–—]+(?=\s)', ' ', t)
    t = re.sub(r'\s+', ' ', t)
    t = re.sub(r'\s+([.,!?;:])', r'\1', t)
    return t.strip()

# ── Noise removal (story body) ───────────────────────────────────────────

_NOISE_LINE_RES = [
    # Social media interaction noise
    re.compile(r'(?i)\bthank(s| you)\b.{0,80}\b(read|award|gold|silver|gild|upvot|'
               r'comment|support|blew up|inbox|kind word|everyone|you all|y.?all|follow|'
               r'share|like|subscribing|sub|fanbase|platform|audience|views)\b'),
    re.compile(r'(?i)\b(this (blew up|got popular|went viral)|wow.{0,25}(blew up|front page)|'
               r'rip my inbox|didn.?t expect this|never expected this|thanks for the|'
               r'appreciate (all )?the|overwhelmed by|response(s)? (has|have) been|'
               r'so many (comment|message|dm|reply|replies)|inbox (is |was )?(full|flooded|blowing))\b'),
    # TL;DR and disclaimers
    re.compile(r'(?i)^\s*(tl;?dr|tldr)\b'),
    re.compile(r'(?i)\b(obligatory\b|on mobile|sorry (for|about) (the |any )?(format|mobile|spelling|grammar|'
               r'typo|english|writing)|english (is|isn.?t|.?s not)\b.{0,25}\bfirst language|'
               r'long[- ]?time lurker|first[- ]?time post(er)?|'
               r'(please |pls )?(be )?(gentle|nice|kind)|not (a )?native (english|speaker)|'
               r'pardon (my|the) (english|grammar|spelling)|apologies? for (the |any )?(grammar|spelling|'
               r'format|typo)|formatting (might be|is) (off|weird|bad)|posting (from|on) mobile)\b'),
    # Cross-references to other posts/parts
    re.compile(r'(?i)\b(my (previous|last|first|original) post|previous part|part\s*\d+\b|'
               r'see my (post|profile|history|page)|as i (mentioned|said|wrote) (in|earlier|before|previously)|'
               r'(link|linked) (in|to) (the |my )?(comment|bio|profile|description)|'
               r'(check out|read|see) (my |the )?(other|previous|last|first) (post|part|story)|'
               r'(more |full )?(story|context|detail|info) (in|on|at) (my|the))\b'),
    # Award/karma farming
    re.compile(r'(?i)\b(edit\s*\d*\s*[:–-]?\s*)?(thanks? (for (the )?)?|thank you (for (the )?)?)'
               r'(all (the )?)?((reddit )?gold|silver|award|platinum|hug|wholesome|helpful|'
               r'kind|upvote|karma|coin|premium|medal)\b'),
    # Call to action / engagement bait
    re.compile(r'(?i)\b(let me know (what you think|in the comments?|your thoughts?)|'
               r'(comment|reply|dm) (below|me|below|your|with)|'
               r'(follow|subscribe|like|share) (for|if|me|this|to)|'
               r'(hit|smash|click) (the )?(like|follow|subscribe|bell|notif)|'
               r'(drop|leave) (a )?(like|comment|follow)|'
               r'(what do you (all |guys |people )?(think|reckon)|your (thoughts?|opinion))\b)'),
    # Sign-offs : UNIQUEMENT si la ligne entière EST l'une de ces formules
    # courtes (pas de match partiel sur une vraie phrase de récit qui
    # commencerait par "Anyway, …").
    re.compile(r"(?i)^\s*(anyway|so yeah|that's all|that's my story|that's it)[.!]?\s*$"),
]
# Demande de jugement (bruit spécifique AITA) — appliqué SEULEMENT sur les
# dernières lignes du texte (cf. strip_post_noise), jamais en plein récit.
_AITA_JUDGMENT_RE = re.compile(
    r'(?i)^\s*(so,?\s*)?(am i (the )?(asshole|jerk|wrong|being unreasonable)|'
    r'aita|aitah|wibta|wita)\??(\s+for\b.{0,60})?\s*\.?\s*$')
_EDIT_HEAD_RE = re.compile(r'(?i)^\s*(edit|update|eta|correction|clarification)\s*\d*\s*[:\-–]')

# Trigger / content warnings — souvent en tête d'histoire ou de titre.
# Le label DOIT être au tout début ("TW", "CW", "Trigger Warning",
# "Content Warning"), pour éviter de matcher "content"/"warehouse" en plein
# récit. On reconnaît deux formes, du plus délimité au moins délimité :
#   1. entre crochets/parenthèses : "[TW: SA]", "(CW: death)" → on retire le bloc.
#   2. label + séparateur + sujets courts, terminé par un point/tiret/fin de ligne :
#      "TW: abuse, violence." / "Trigger Warning - self harm" → on retire le préambule.
# La liste de sujets est volontairement bornée (pas de point, ni ; : — comme fin)
# pour ne JAMAIS dévorer la première phrase du récit.
_TW_LABEL = r'(?:tw|cw|trigger\s*warning|content\s*warning|trigger|content\s+warning)'
_TW_BRACKET_RE = re.compile(
    r'(?i)^\s*[\[(]\s*' + _TW_LABEL + r'\b[^\])]{0,80}?[\])]\s*[:.\-–—]?\s*')
_TW_INLINE_RE = re.compile(
    r'(?i)^\s*' + _TW_LABEL + r'\b\s*'
    r'[:\-–—]\s*'                                  # séparateur OBLIGATOIRE (label nu = ambigu)
    r'[^.;:\n]{0,80}?'                             # sujets courts, sans ponctuation forte
    r'\s*(?=[.\-–—]\s|$)'                          # s'arrête avant un point/tiret ou fin de ligne
    r'[.\-–—]?\s*')
# Label "nu" : la ligne ne contient QUE l'avertissement (label + sujets courts),
# sans récit derrière. Sert à reconnaître une ligne dédiée à jeter entièrement.
_TW_BARE_LINE_RE = re.compile(
    r'(?i)^\s*[\[(]?\s*' + _TW_LABEL + r'\b[^\n]{0,60}?[\])]?\s*[.:]?\s*$')


def strip_trigger_warnings(text: str) -> str:
    """Retire les trigger/content warnings en début de texte (et lignes dédiées).

    Ne touche qu'au préambule : un "TW: abuse, violence" en tête, ou une ligne
    entière qui n'est qu'un avertissement. Le corps du récit reste intact.
    """
    if not text:
        return ''
    out_lines = []
    started = False
    for line in text.split('\n'):
        s = line.strip()
        if not started and s:
            # On retire d'abord un préambule délimité (crochets puis forme inline).
            stripped = _TW_BRACKET_RE.sub('', line, count=1)
            if stripped == line:
                stripped = _TW_INLINE_RE.sub('', line, count=1)
            if stripped != line and stripped.strip():
                # Avertissement en tête + récit derrière → on garde le récit.
                out_lines.append(stripped.lstrip())
                started = True
                continue
            if not stripped.strip() or _TW_BARE_LINE_RE.match(s):
                # Ligne dédiée (rien d'utile après retrait, ou label nu) → jetée.
                continue
            started = True
        out_lines.append(line)
    return '\n'.join(out_lines).strip()


def strip_post_noise(text: str) -> str:
    """Remove non-story content: thanks, TL;DR, disclaimers, edit appendices, CTAs."""
    if not text:
        return ''
    lines = text.split('\n')
    n = max(len(lines), 1)
    last_block = n - 3  # la demande de jugement AITA n'est coupée qu'ici (fin)
    kept = []
    for idx, line in enumerate(lines):
        s = line.strip()
        if not s:
            kept.append(line)
            continue
        if _EDIT_HEAD_RE.match(s) and idx >= n * 0.45:
            break
        if len(s) < 300 and any(rx.search(s) for rx in _NOISE_LINE_RES):
            continue
        # Demande de jugement ("Am I the asshole?") : bruit seulement en toute
        # fin de post. En plein récit, c'est une vraie phrase → on la garde.
        if idx >= last_block and len(s) < 300 and _AITA_JUDGMENT_RE.match(s):
            continue
        kept.append(line)
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(kept)).strip()


_HOOK_SIGNALS = re.compile(
    r'\b(never|always|shocked|terrified|realized|discovered|turns out|'
    r'plot twist|secret|lied|betrayed|caught|confession|ruined|worst|best|'
    r'incredible|insane|unbelievable|finally|broke up|cheated|fired|quit|'
    r'confronted|screamed|cried|suddenly|then i saw|i found out|the truth|'
    r'nobody knew|i was wrong|turned out|little did|to my horror|froze|'
    r'heart stopped|refused|demanded|threatened|exposed|humiliated|revenge|'
    r'karma|walked out|kicked out|called the police|never spoke again|'
    r'whispered|footsteps|in the dark|behind me|watching me|disappeared|'
    r'blood|scream|knocking|alone|woke up)\b',
    re.IGNORECASE,
)

_SOURCE_LABELS = {
    'AmItheAsshole': 'r/AmItheAsshole', 'AITAH': 'r/AITAH',
    'relationship_advice': 'r/relationship_advice', 'relationships': 'r/relationships',
    'tifu': 'r/tifu', 'TrueOffMyChest': 'r/TrueOffMyChest',
    'confession': 'r/confession', 'offmychest': 'r/offmychest',
    'pettyrevenge': 'r/pettyrevenge', 'ProRevenge': 'r/ProRevenge',
    'MaliciousCompliance': 'r/MaliciousCompliance',
    'raisedbynarcissists': 'r/raisedbynarcissists',
    'EntitledParents': 'r/EntitledParents', 'JUSTNOMIL': 'r/JUSTNOMIL',
    'todayilearned': 'r/todayilearned', 'interestingasfuck': 'r/interestingasfuck',
    'Damnthatsinteresting': 'r/Damnthatsinteresting',
    'wattpad': 'Wattpad', 'quora': 'Quora',
    'facts': 'Did You Know',
}

def _source_line(post: Dict) -> str:
    sub = post.get('subreddit') or post.get('source') or ''
    label = _SOURCE_LABELS.get(sub) or (f'r/{sub}' if sub else 'Reddit')
    return f'Story from {label}.'


class ContentScraper:

    def __init__(self, config):
        self.config = config
        self.providers = RedditProviderChain(
            user_agent=getattr(config, 'REDDIT_USER_AGENT', None)
        )

    def _extract_hook(self, text: str) -> str:
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        candidates = [s.strip() for s in sentences if 40 <= len(s.strip()) <= 150]
        if not candidates:
            return text.strip()[:100]
        candidates.sort(key=lambda s: len(_HOOK_SIGNALS.findall(s)), reverse=True)
        return candidates[0]

    def _quality_score(self, post: Dict) -> float:
        engagement = math.log(post['score'] + 1) / math.log(10001)
        wc = post['words']
        if STORY_OPTIMAL_MIN <= wc <= STORY_OPTIMAL_MAX:
            length_score = 1.0
        elif wc < STORY_OPTIMAL_MIN:
            length_score = wc / STORY_OPTIMAL_MIN
        else:
            length_score = max(0.0, 1.0 - (wc - STORY_OPTIMAL_MAX) / STORY_OPTIMAL_MAX)
        return 0.6 * engagement + 0.4 * length_score

    def _fetch_posts_providers(self, subreddit: str, limit: int, seen_ids: Set[str],
                               max_words: Optional[int] = None,
                               time_filter: str = 'month', min_score: int = 0,
                               seen_titles: Optional[Set[str]] = None) -> List[Dict]:
        try:
            raw_posts = self.providers.fetch(
                subreddit, limit, time_filter=time_filter,
                min_score=min_score, seen_ids=seen_ids,
            )
        except Exception as e:
            logger.error(f'Provider chain failed for r/{subreddit}: {e}')
            return []

        seen_titles = seen_titles or set()
        candidates = []
        for post in raw_posts:
            title, text = post['title'], post['text']
            if title_fingerprint(title) in seen_titles:
                continue
            wc = len((title + ' ' + text).split())
            if max_words is not None and (wc < STORY_MIN_WORDS or wc > max_words):
                continue
            if not _is_safe(title + ' ' + text):
                continue
            ref = text or title
            if sum(c.isascii() for c in ref) / max(len(ref), 1) < 0.8:
                continue
            post['words'] = wc
            candidates.append(post)

        if max_words is not None:
            candidates.sort(key=self._quality_score, reverse=True)
        return candidates[:limit]

    def _fetch_from_pool(self, subreddits: List[str], limit: int, seen_ids: Set[str],
                         max_words: Optional[int], time_filter: str, min_score: int,
                         seen_titles: Optional[Set[str]] = None,
                         extra_fetchers: Optional[List] = None) -> List[Dict]:
        pooled: List[Dict] = []
        seen_post_ids    = set(seen_ids)
        seen_post_titles = set(seen_titles or set())

        for rnd, cfg in enumerate([{'time_filter': time_filter, 'min_score': min_score}]
                                   + ADAPTIVE_WIDENING, start=1):
            if len(pooled) >= limit:
                break
            rtf    = cfg['time_filter']
            rscore = min(cfg['min_score'], min_score)
            need   = limit - len(pooled)
            subs   = random.sample(subreddits, len(subreddits))
            snap_ids, snap_titles = set(seen_post_ids), set(seen_post_titles)

            executor = ThreadPoolExecutor(max_workers=POOL_BATCH)
            try:
                futures = {
                    executor.submit(self._fetch_posts_providers, sub, need,
                                    snap_ids, max_words, rtf, rscore, snap_titles): sub
                    for sub in subs
                }
                for fut in as_completed(futures):
                    try:
                        res = fut.result() or []
                    except Exception as e:
                        logger.warning(f'Parallel fetch failed r/{futures[fut]}: {type(e).__name__}')
                        res = []
                    for p in res:
                        fp = title_fingerprint(p['title'])
                        if p['id'] not in seen_post_ids and fp not in seen_post_titles:
                            seen_post_ids.add(p['id'])
                            seen_post_titles.add(fp)
                            pooled.append(p)
                    if len(pooled) >= limit:
                        break
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

            logger.info(f'Round {rnd} ({rtf}, min_score={rscore}): {len(pooled)}/{limit}')

        if len(pooled) < limit and extra_fetchers:
            need = limit - len(pooled)
            snap_ids = set(seen_post_ids)
            results = []
            with ThreadPoolExecutor(max_workers=max(len(extra_fetchers), 1)) as ex:
                for fut in as_completed([ex.submit(fn, need, snap_ids) for fn in extra_fetchers]):
                    try:
                        results.extend(fut.result() or [])
                    except Exception as e:
                        logger.warning(f'External source failed: {type(e).__name__}')
            for p in results:
                fp = title_fingerprint(p['title'])
                if p['id'] not in seen_post_ids and fp not in seen_post_titles:
                    seen_post_ids.add(p['id'])
                    seen_post_titles.add(fp)
                    pooled.append(p)

        pooled.sort(key=self._quality_score, reverse=True)
        logger.info(f'Pool {subreddits[:3]}…: {len(pooled)} → top {min(limit, len(pooled))}')
        return pooled[:limit]

    def _external_story_fetchers(self, ctype: str) -> List:
        ua = self.config.REDDIT_USER_AGENT
        if ctype == 'drama':
            return [
                lambda need, seen: fetch_wattpad('cheating relationship drama',
                                                 need, seen, ua, _is_safe,
                                                 STORY_MIN_WORDS, STORY_MAX_WORDS),
                lambda need, seen: fetch_quora('Relationships', need, seen,
                                               ua, _is_safe, 150, STORY_MAX_WORDS),
            ]
        return []

    _FACT_APIS = [
        ('https://uselessfacts.jsph.pl/api/v2/facts/random?language=en', lambda j: j.get('text')),
        ('https://uselessfacts.jsph.pl/random.json?language=en',         lambda j: j.get('text')),
        ('https://api.api-ninjas.com/v1/facts',
         lambda j: (j[0].get('fact') if isinstance(j, list) and j else None)),
        ('https://catfact.ninja/fact', lambda j: j.get('fact')),
    ]

    def _fetch_one_api_fact(self, idx: int) -> Optional[str]:
        url, extract = self._FACT_APIS[idx % len(self._FACT_APIS)]
        try:
            r = requests.get(url, headers={'User-Agent': self.config.REDDIT_USER_AGENT}, timeout=6)
            if r.status_code != 200:
                return None
            fact = extract(r.json())
            return fact.strip() if fact and fact.strip() else None
        except Exception:
            return None

    def _fetch_api_facts_parallel(self, count: int) -> List[str]:
        if count <= 0:
            return []
        n_req = min(count * 2 + 4, 64)
        out: List[str] = []
        seen: Set[str] = set()
        with ThreadPoolExecutor(max_workers=min(n_req, 12)) as ex:
            for fut in as_completed([ex.submit(self._fetch_one_api_fact, i) for i in range(n_req)]):
                fact = fut.result()
                if not fact:
                    continue
                key = fact.lower().strip()
                if key not in seen:
                    seen.add(key)
                    out.append(fact)
                    if len(out) >= count:
                        break
        return out

    def _make_story_item(self, post: Dict) -> Dict:
        title  = strip_trigger_warnings((post.get('title') or '').strip())
        body   = strip_post_noise(strip_trigger_warnings((post.get('text') or '').strip()))
        # Le titre est NARRÉ EN PREMIER : il est lu pendant que la carte Reddit est
        # affichée (carte masquée pile quand le titre est fini, cf. whisper_sub).
        # Le corps suit. Le titre stocké pour la carte = exactement ce préfixe.
        raw    = (title + '. ' + body).strip() if title else body
        script = censor(clean_tts_text(raw))
        # Append source attribution
        script = script.rstrip('.') + '. ' + _source_line(post)
        post['tts_script']       = script
        post['content_strategy'] = 'story'
        # Apply stronger censorship to title for card display
        post['title']            = censor(clean_tts_text(title))
        post['hook']             = self._extract_hook(script)
        return post

    def _make_facts_batch(self, posts: List[Dict], fallback_facts: List[str]) -> List[Dict]:
        raw_facts: List[tuple] = []
        seen_facts: Set[str]   = set()
        for p in posts:
            t = re.sub(r'^TIL\s+(that\s+)?', '', p['title'], flags=re.I).strip()
            if t:
                raw_facts.append((t[0].upper() + t[1:], p.get('id')))
        raw_facts.extend((f, None) for f in fallback_facts)
        deduped: List[tuple] = []
        for text, sid in raw_facts:
            key = text.lower().strip()
            if key and key not in seen_facts:
                seen_facts.add(key)
                deduped.append((text, sid))

        batches: List[List[tuple]] = []
        current: List[tuple] = []
        word_count = 0
        for fact in deduped:
            current.append(fact)
            word_count += len(fact[0].split())
            if len(current) >= FACTS_MIN_COUNT and (
                    word_count >= FACTS_BATCH_WORDS or len(current) >= FACTS_MAX_COUNT):
                batches.append(current)
                current, word_count = [], 0
        if current:
            if len(current) >= FACTS_MIN_COUNT or not batches:
                batches.append(current)
            else:
                batches[-1].extend(current)
        return [self._finalize_facts_batch(b) for b in batches]

    def _finalize_facts_batch(self, facts: List[tuple]) -> Dict:
        texts      = [t for t, _ in facts]
        source_ids = [sid for _, sid in facts if sid]
        body       = '... '.join(t.rstrip('.') + '.' for t in texts)
        script     = censor(clean_tts_text('Did you know? ... ' + body))
        script    += ' Source: r/todayilearned.'
        batch_id   = 'facts_' + str(abs(hash(body)) % (10 ** 10))
        return {
            'id': batch_id,
            'consumed_ids': source_ids + [batch_id],
            'title': 'Did You Know?',
            'text': body,
            'source': 'facts',
            'subreddit': 'todayilearned',
            'tts_script': script,
            'content_strategy': 'facts',
            'hook': self._extract_hook(texts[0]) if texts else 'Did you know?',
        }

    def _top_up_from_reserve(self, ctype: str, used_subs: List[str], posts: List[Dict],
                             need: int, seen_ids: Set[str],
                             seen_titles: Set[str]) -> List[Dict]:
        """Complète `posts` avec les subreddits de RÉSERVE (stand-by) quand les
        sources principales n'ont pas rempli le quota. Activé seulement en cas de
        pénurie → flux d'histoires neuves durable sans ralentir le cas nominal."""
        reserve = [s for s in getattr(self.config, 'EXTRA_SUBREDDITS', {}).get(ctype, [])
                   if s not in used_subs]
        if not reserve:
            return posts
        logger.info(f'Sources principales en baisse ({len(posts)}/{need}) — '
                    f'activation de {len(reserve)} sources de réserve (stand-by).')
        used_ids    = set(seen_ids) | {p['id'] for p in posts}
        used_titles = set(seen_titles) | {title_fingerprint(p['title']) for p in posts}
        more = self._fetch_from_pool(
            reserve, need - len(posts), used_ids,
            max_words=STORY_MAX_WORDS, time_filter=STORY_TIME_FILTER,
            min_score=STORY_MIN_SCORE, seen_titles=used_titles,
            extra_fetchers=self._external_story_fetchers(ctype),
        )
        return posts + more

    def get_content(self, limit: int, seen_ids: Set[str], content_type: str,
                    seen_titles: Optional[Set[str]] = None) -> List[Dict]:
        seen_titles = seen_titles or set()
        type_map    = self.config.CONTENT_TYPE_MAP

        if content_type == 'rotate':
            types = list(type_map.keys())
        elif content_type in type_map:
            types = [content_type]
        else:
            logger.warning(f'Unknown type "{content_type}" — falling back to facts')
            types = ['facts']

        items: List[Dict] = []
        ti = 0
        while len(items) < limit and ti < len(types) * 4:
            ctype    = types[ti % len(types)]
            ti      += 1
            cfg      = type_map[ctype]
            sub_pool = cfg.get('subreddits', [cfg['subreddit']])
            need     = limit - len(items)
            strategy = cfg['strategy']

            if strategy == 'story':
                posts = self._fetch_from_pool(
                    sub_pool, need, seen_ids,
                    max_words=STORY_MAX_WORDS,
                    time_filter=STORY_TIME_FILTER,
                    min_score=STORY_MIN_SCORE,
                    seen_titles=seen_titles,
                    extra_fetchers=self._external_story_fetchers(ctype),
                )
                # Fetcher STAND-BY : si les sources principales se tarissent (pool
                # incomplet, p. ex. historique anti-répétition saturé), on active
                # dynamiquement les subreddits de réserve pour compléter le quota.
                if len(posts) < need:
                    posts = self._top_up_from_reserve(
                        ctype, sub_pool, posts, need, seen_ids, seen_titles)
                if posts:
                    items.extend(self._make_story_item(p) for p in posts)
                else:
                    logger.warning(f'All story sources exhausted for {ctype} — falling back to facts.')
                    strategy = 'facts'

            if strategy == 'facts':
                facts_pool = type_map['facts'].get('subreddits', ['todayilearned'])
                snap_ids, snap_titles = set(seen_ids), set(seen_titles)
                raw_results: List[Dict] = []
                with ThreadPoolExecutor(max_workers=8) as ex:
                    futures = {
                        ex.submit(self._fetch_posts_providers, sub, need * 12,
                                  snap_ids, None, 'month', 0, snap_titles): sub
                        for sub in facts_pool
                    }
                    for fut in as_completed(futures):
                        try:
                            raw_results.extend(fut.result() or [])
                        except Exception as e:
                            logger.warning(f'Facts fetch failed r/{futures[fut]}: {type(e).__name__}')

                posts = []
                facts_seen        = set(seen_ids)
                facts_seen_titles = set(seen_titles)
                for p in raw_results:
                    if p['id'] not in facts_seen:
                        facts_seen.add(p['id'])
                        facts_seen_titles.add(title_fingerprint(p['title']))
                        posts.append(p)

                have_words = sum(len(p['title'].split()) for p in posts)
                missing    = max(need * FACTS_MIN_COUNT - len(posts),
                                 (need * FACTS_BATCH_WORDS - have_words) // 12)
                fallback   = self._fetch_api_facts_parallel(missing + 2)
                items.extend(self._make_facts_batch(posts, fallback)[:need])

            if content_type != 'rotate':
                break

        logger.info(f'Total items: {len(items[:limit])} (type={content_type})')
        return items[:limit]
