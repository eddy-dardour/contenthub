#!/usr/bin/env python3
"""
TikTok uploader — Content Posting API v2 (flux en 3 étapes)
Docs : https://developers.tiktok.com/doc/content-posting-api-get-started
"""

import logging
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_API_BASE = 'https://open.tiktokapis.com/v2'
_INIT_URL = f'{_API_BASE}/post/publish/video/init/'
_STATUS_URL = f'{_API_BASE}/post/publish/status/fetch/'


class TikTokUploader:

    def __init__(self, config):
        self.access_token = config.TIKTOK_SESSION_ID  # token OAuth ou sessionid
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json; charset=UTF-8',
        })

    # ── Public ───────────────────────────────────────────────────────

    def upload(self, video_path: str, caption: str) -> bool:
        video_file = Path(video_path)
        if not video_file.exists():
            logger.error(f'Fichier vidéo introuvable : {video_path}')
            return False

        file_size = video_file.stat().st_size
        logger.info(f'Upload : {video_file.name} ({file_size / 1_048_576:.1f} Mo)')

        publish_id = self._init_upload(caption, file_size)
        if not publish_id:
            return False

        # L'étape init renvoie aussi l'upload_url dans certaines réponses ;
        # ici on utilise le flux FILE_UPLOAD (chunks).
        if not self._send_video(video_file, file_size):
            return False

        return self._wait_for_publish(publish_id)

    # ── Étape 1 : init ───────────────────────────────────────────────

    def _init_upload(self, caption: str, file_size: int):
        payload = {
            'post_info': {
                'title': caption[:2200],
                'privacy_level': 'PUBLIC_TO_EVERYONE',
                'disable_duet': False,
                'disable_comment': False,
                'disable_stitch': False,
            },
            'source_info': {
                'source': 'FILE_UPLOAD',
                'video_size': file_size,
                'chunk_size': file_size,   # upload en un seul chunk
                'total_chunk_count': 1,
            },
        }
        try:
            resp = self.session.post(_INIT_URL, json=payload, timeout=20)
            data = resp.json()
            logger.debug(f'Init réponse : {data}')

            if resp.status_code != 200 or data.get('error', {}).get('code') != 'ok':
                err = data.get('error', data)
                logger.error(f'Échec init upload : {err}')
                return None

            inner = data.get('data', {})
            self._upload_url = inner.get('upload_url')
            publish_id = inner.get('publish_id')

            if not self._upload_url or not publish_id:
                logger.error(f'Réponse init incomplète : {data}')
                return None

            logger.info(f'Init OK — publish_id : {publish_id}')
            return publish_id

        except Exception as e:
            logger.error(f'Erreur init upload : {e}')
            return None

    # ── Étape 2 : envoi des octets ───────────────────────────────────

    def _send_video(self, video_file: Path, file_size: int) -> bool:
        try:
            with open(video_file, 'rb') as f:
                video_bytes = f.read()

            headers = {
                'Content-Type': 'video/mp4',
                'Content-Range': f'bytes 0-{file_size - 1}/{file_size}',
                'Content-Length': str(file_size),
            }
            resp = requests.put(self._upload_url, data=video_bytes,
                                headers=headers, timeout=300)
            if resp.status_code in (200, 201):
                logger.info('Vidéo envoyée avec succès.')
                return True
            logger.error(f'Échec envoi vidéo : HTTP {resp.status_code} — {resp.text[:300]}')
            return False

        except Exception as e:
            logger.error(f'Erreur envoi vidéo : {e}')
            return False

    # ── Étape 3 : attente publication ────────────────────────────────

    def _wait_for_publish(self, publish_id: str, timeout: int = 120) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = self.session.post(
                    _STATUS_URL,
                    json={'publish_id': publish_id},
                    timeout=15,
                )
                data = resp.json()
                status = data.get('data', {}).get('status', '')
                logger.info(f'Statut publication : {status}')

                if status == 'PUBLISH_COMPLETE':
                    logger.info('Publication réussie !')
                    return True
                if status in ('FAILED', 'PUBLISH_FAILED'):
                    reason = data.get('data', {}).get('fail_reason', '?')
                    logger.error(f'Publication échouée : {reason}')
                    return False

            except Exception as e:
                logger.warning(f'Erreur vérification statut : {e}')

            time.sleep(5)

        logger.error('Timeout en attente de publication.')
        return False
