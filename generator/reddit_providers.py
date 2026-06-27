#!/usr/bin/env python3
"""
reddit_providers.py — Backends Reddit GRATUITS et SANS authentification.

Pourquoi : l'API Reddit officielle (praw/OAuth) et les endpoints JSON publics
(www/old/api.reddit.com) sont bloqués (403) depuis les IP datacenter, et créer
une app OAuth peut échouer. Ces backends tiers donnent accès aux VRAIS posts
Reddit (titres, textes, scores) sans aucun identifiant.

═══════════════════════════════════════════════════════════════════════════
  POUR CHANGER / RÉORDONNER LES BACKENDS : édite simplement PROVIDER_ORDER
  ci-dessous. Le scraper essaie les providers dans cet ordre et s'arrête au
  premier qui renvoie des posts. Commente une ligne pour désactiver un backend.
═══════════════════════════════════════════════════════════════════════════

Backends inclus (testés en direct) :
  • PullPush      ⭐ successeur de Pushshift. VRAIS scores (histoires 40k+
                     upvotes), tri par score réel. LE MEILLEUR pour le viral.
  • ArcticShift      archive Reddit rapide et fiable. Tri par date ; scores
                     archivés plus bas mais posts réels et complets.
  • AllOrigins       proxy CORS → reddit.com/*.json (best-effort, intermittent).
  • CorsProxy        idem via corsproxy.io (best-effort).
  • JinaProxy        idem via r.jina.ai (best-effort).

Chaque provider expose la même interface :
    fetch(subreddit, limit, time_filter, min_score) -> List[dict]
et renvoie des dicts au format normalisé :
    {id, title, text, score, author, subreddit, source, over_18}
"""

import json
import logging
import time
from typing import List, Dict, Optional

import requests

logger = logging.getLogger(__name__)

# User-Agent navigateur : certains backends rejettent "python-requests".
_BROWSER_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
               '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')


def _normalize(raw: dict, subreddit: str, source: str) -> Optional[Dict]:
    """Convertit un post brut (toutes sources) vers le format normalisé du bot.

    Renvoie None si le post est inexploitable (supprimé, sans contenu).
    """
    post_id = raw.get('id')
    if not post_id:
        return None
    title = (raw.get('title') or '').strip()
    text = (raw.get('selftext') or '').strip()
    if text in ('[removed]', '[deleted]'):
        text = ''
    # Post sans texte ET titre court = inexploitable comme histoire.
    if not text and len(title) < 80:
        return None
    author = raw.get('author') or '[deleted]'
    return {
        'id': post_id,
        'title': title,
        'text': text,
        'score': int(raw.get('score') or 0),
        'author': author,
        'subreddit': raw.get('subreddit') or subreddit,
        'source': source,
        'over_18': bool(raw.get('over_18')),
    }


# ─────────────────────────────────────────────────────────────────────────
#  Providers
# ─────────────────────────────────────────────────────────────────────────

class BaseProvider:
    """Interface commune. Sous-classer et implémenter `fetch`."""

    name = 'base'
    # best_effort=True : provider peu fiable (proxies CORS). Ses échecs sont
    # attendus → loggés en DEBUG, pas en WARNING (évite de polluer les logs).
    best_effort = False

    def __init__(self, user_agent: Optional[str] = None, timeout: int = 20):
        self.headers = {'User-Agent': user_agent or _BROWSER_UA}
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def _get_with_retry(self, url: str, params: Optional[dict] = None,
                        timeout: Optional[int] = None,
                        attempts: int = 3) -> requests.Response:
        """GET avec retry exponentiel (1s puis 2s) sur 429/5xx et erreurs réseau.

        Ne réessaie PAS sur un 4xx définitif (404, 403…) : on lève alors
        l'exception pour que RedditProviderChain passe au provider suivant.
        `attempts=1` désactive le retry (proxies best-effort à échec rapide).
        """
        timeout = timeout or self.timeout
        for attempt in range(attempts):
            try:
                r = self.session.get(url, params=params, timeout=timeout)
                r.raise_for_status()
                return r
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                transient = status == 429 or (status is not None and status >= 500)
                if transient and attempt < attempts - 1:
                    time.sleep(2 ** attempt)  # 1s, 2s
                    continue
                raise
            except requests.RequestException:
                # Timeout / erreur de connexion : transitoire → on retente.
                if attempt < attempts - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise

    @staticmethod
    def _safe_json(resp: requests.Response) -> dict:
        """Parse JSON sans jamais lever : un proxy mort renvoie souvent du HTML
        (page d'erreur, captcha) → .json() lèverait JSONDecodeError. On renvoie
        alors {} pour que le provider remonte simplement 0 post (pas d'erreur)."""
        try:
            return resp.json()
        except ValueError:  # JSONDecodeError est une sous-classe de ValueError
            return {}

    def fetch(self, subreddit: str, limit: int, time_filter: str = 'month',
              min_score: int = 0) -> List[Dict]:
        raise NotImplementedError


class PullPushProvider(BaseProvider):
    """⭐ PullPush — successeur de Pushshift. Recherche full-archive avec tri par
    score RÉEL. Surface les histoires les plus virales de tous les temps
    (idéal TikTok). Aucune auth. https://pullpush.io
    """

    name = 'pullpush'
    URL = 'https://api.pullpush.io/reddit/search/submission/'
    # best_effort : PullPush est souvent lent/indisponible. Ses timeouts loggent
    # en DEBUG (pas WARNING) et la cascade enchaîne immédiatement sur le suivant.
    best_effort = True

    def fetch(self, subreddit, limit, time_filter='month', min_score=0):
        # On scanne large et trié par score décroissant : PullPush renvoie
        # directement le top all-time du sub, ce qui maximise le viral.
        params = {
            'subreddit': subreddit,
            'size': min(max(limit * 25, 50), 100),  # max 100 côté API
            'sort': 'desc',
            'sort_type': 'score',
        }
        # PullPush peut être lent sur les très gros subs. ArcticShift (provider #1)
        # couvre déjà l'essentiel ; PullPush n'est qu'un complément. On le borne
        # donc à 12s en une seule tentative : s'il traîne, on l'abandonne vite et
        # la cascade continue — bien plus rapide qu'un long timeout bloquant.
        r = self._get_with_retry(self.URL, params=params, timeout=8, attempts=1)
        data = self._safe_json(r).get('data', [])
        out = []
        for raw in data:
            p = _normalize(raw, subreddit, self.name)
            if p and p['score'] >= min_score:
                out.append(p)
        return out


class ArcticShiftProvider(BaseProvider):
    """Arctic Shift — archive Reddit publique rapide et fiable. Tri par date
    (pas de tri par score côté API ; scores archivés plus bas). Posts complets.
    https://arctic-shift.photon-reddit.com
    """

    name = 'arctic_shift'
    URL = 'https://arctic-shift.photon-reddit.com/api/posts/search'

    def fetch(self, subreddit, limit, time_filter='month', min_score=0):
        params = {
            'subreddit': subreddit,
            'limit': min(max(limit * 25, 50), 100),
            'sort': 'desc',  # plus récents d'abord (seul tri supporté)
            'fields': 'id,title,selftext,score,author,subreddit,over_18',
        }
        r = self._get_with_retry(self.URL, params=params)
        data = self._safe_json(r).get('data', [])
        out = []
        # Arctic Shift ne trie PAS par score (seulement par date). On applique
        # le seuil demandé quand il est significatif (>5) ; sinon plancher à 5
        # pour filtrer les posts sans validation.
        score_floor = max(min_score, 5)
        for raw in data:
            p = _normalize(raw, subreddit, self.name)
            if p and p['score'] >= score_floor:
                out.append(p)
        return out


class DirectRedditProvider(BaseProvider):
    """Accès DIRECT à Reddit via old.reddit.com/{sub}/top.json.

    Reddit bloque les IP datacenter (403) mais répond normalement aux IP
    RÉSIDENTIELLES : depuis une machine perso, c'est la source la plus fiable et
    la plus fraîche (vrais scores, posts du jour). Placé en tête de cascade.
    """

    name = 'direct_reddit'
    # Reddit bloque les IP datacenter (403) mais répond aux IP résidentielles.
    # best_effort : si ça 403 sur la machine de l'utilisateur, échec silencieux.
    best_effort = True

    def fetch(self, subreddit, limit, time_filter='month', min_score=0):
        size = min(max(limit * 10, 25), 100)
        url = (f'https://old.reddit.com/r/{subreddit}/top.json'
               f'?t={time_filter}&limit={size}&raw_json=1')
        r = self._get_with_retry(url, timeout=8, attempts=1)  # fail-fast
        children = self._safe_json(r).get('data', {}).get('children', [])
        out = []
        for item in children:
            p = _normalize(item.get('data', {}), subreddit, self.name)
            if p and p['score'] >= min_score:
                out.append(p)
        return out


class _CorsRedditProvider(BaseProvider):
    """Base pour les proxies CORS qui relaient reddit.com/{sub}/top.json.

    Best-effort : Reddit bloque souvent les IP de ces proxies (403/5xx), donc
    placés en bas de PROVIDER_ORDER. Sous-classes : définir `build_url`.
    """

    best_effort = True

    def build_url(self, reddit_url: str) -> str:
        raise NotImplementedError

    def _unwrap(self, resp: requests.Response) -> dict:
        """Par défaut la réponse EST le JSON Reddit. Surcharger si encapsulé."""
        return self._safe_json(resp)

    def fetch(self, subreddit, limit, time_filter='month', min_score=0):
        reddit_url = (f'https://www.reddit.com/r/{subreddit}/top.json'
                      f'?t={time_filter}&limit={min(max(limit * 10, 25), 100)}&raw_json=1')
        # Proxies best-effort souvent morts : échec rapide (1 tentative, timeout
        # court) pour ne pas plomber la latence quand ils ne répondent pas.
        r = self._get_with_retry(self.build_url(reddit_url), timeout=6, attempts=1)
        payload = self._unwrap(r)
        children = payload.get('data', {}).get('children', [])
        out = []
        for item in children:
            raw = item.get('data', {})
            p = _normalize(raw, subreddit, self.name)
            if p and p['score'] >= min_score:
                out.append(p)
        return out


class AllOriginsProvider(_CorsRedditProvider):
    """Proxy CORS allorigins.win (encapsule la réponse dans {contents:...})."""

    name = 'allorigins'

    def build_url(self, reddit_url):
        from urllib.parse import quote
        return f'https://api.allorigins.win/get?url={quote(reddit_url, safe="")}'

    def _unwrap(self, resp):
        wrap = self._safe_json(resp)
        try:
            return json.loads(wrap.get('contents') or '{}')
        except (ValueError, TypeError):
            return {}


class CorsProxyProvider(_CorsRedditProvider):
    """Proxy CORS corsproxy.io (passthrough direct)."""

    name = 'corsproxy'

    def build_url(self, reddit_url):
        from urllib.parse import quote
        return f'https://corsproxy.io/?url={quote(reddit_url, safe="")}'


class JinaProxyProvider(_CorsRedditProvider):
    """Proxy r.jina.ai (passthrough, parfois renvoie du texte → on tente JSON)."""

    name = 'jina'

    def build_url(self, reddit_url):
        return f'https://r.jina.ai/{reddit_url}'


# ─────────────────────────────────────────────────────────────────────────
#  ⚙️  ORDRE DES BACKENDS — édite ICI pour changer/réordonner/désactiver
# ─────────────────────────────────────────────────────────────────────────
#  Le scraper essaie ces providers dans l'ordre et garde le premier qui
#  renvoie des posts. Mets le meilleur en premier. Commente une ligne (#)
#  pour désactiver un backend.

PROVIDER_ORDER = [
    ArcticShiftProvider,   # ⭐ archive fiable et rapide — priorité 1
    PullPushProvider,      # vrais scores top all-time (fallback complémentaire)
    DirectRedditProvider,  # accès direct (marche sur IP résidentielle) — best-effort
    AllOriginsProvider,    # proxy CORS → reddit.com (best-effort)
    CorsProxyProvider,     # proxy CORS → reddit.com (best-effort)
    JinaProxyProvider,     # proxy texte → reddit.com (best-effort)
]


class RedditProviderChain:
    """Cascade de backends Reddit : parcourt PROVIDER_ORDER et ACCUMULE les posts
    jusqu'à atteindre `limit`.

    Contrairement à un simple « premier qui répond gagne », la cascade continue
    tant qu'elle n'a pas assez de posts : si PullPush n'en remonte que 3 alors
    qu'on en veut 10, elle complète avec Arctic Shift puis les proxies. Les posts
    sont dédoublonnés par `id` entre providers. Elle ne s'arrête tôt que lorsque
    `limit` est atteint (évite des appels réseau inutiles).

    Usage :
        chain = RedditProviderChain(user_agent='...')
        posts = chain.fetch('AmItheAsshole', limit=10, min_score=2000)
    """

    def __init__(self, user_agent: Optional[str] = None,
                 providers: Optional[list] = None, timeout: int = 30):
        classes = providers if providers is not None else PROVIDER_ORDER
        self.providers = [cls(user_agent=user_agent, timeout=timeout)
                          for cls in classes]

    def fetch(self, subreddit: str, limit: int, time_filter: str = 'month',
              min_score: int = 0, seen_ids: Optional[set] = None) -> List[Dict]:
        seen = set(seen_ids) if seen_ids else set()
        collected: List[Dict] = []
        for i, prov in enumerate(self.providers):
            try:
                posts = prov.fetch(subreddit, limit, time_filter, min_score)
                added = 0
                for p in posts:
                    if p['id'] in seen or p['over_18']:
                        continue
                    seen.add(p['id'])
                    collected.append(p)
                    added += 1
                if added:
                    logger.info(f'r/{subreddit} via {prov.name} : +{added} posts '
                                f'(total {len(collected)}/{limit}, provider #{i+1})')
                else:
                    logger.debug(f'r/{subreddit} via {prov.name} : 0 nouveau post — '
                                 f'essai du provider suivant')
            except Exception as e:
                # Les proxies best-effort échouent souvent (403/timeout) : c'est
                # attendu → DEBUG. Seuls les providers fiables loggent en WARNING.
                log = logger.debug if prov.best_effort else logger.warning
                log(f'Provider {prov.name} échoué pour r/{subreddit} : '
                    f'{type(e).__name__} — essai du suivant')
            # Assez de posts collectés : inutile d'appeler les providers suivants.
            if len(collected) >= limit:
                return collected[:limit]
            if i < len(self.providers) - 1:
                time.sleep(0.05)
        if collected:
            logger.info(f'r/{subreddit} : cascade complète → {len(collected)} posts '
                        f'(objectif {limit}, tous providers épuisés)')
        else:
            # En usage pool (des dizaines de subs, on n'en garde que `limit`),
            # qu'un sub donné soit vide est normal → DEBUG, pas ERROR. L'absence
            # TOTALE de contenu est détectée et signalée plus haut (scraper/main).
            logger.debug(f'r/{subreddit} : aucun post via les providers')
        return collected
