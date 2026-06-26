# AI-generated: Serializd API client with bidirectional sync support
"""Serializd API client for authentication and data sync."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from platformdirs import user_data_dir

from trakt_serializd_sync.consts import (
    DEFAULT_SERIALIZD_DELAY_MS,
    SERIALIZD_APP_ID,
    SERIALIZD_BASE_URL,
    SERIALIZD_FRONT_URL,
)
from trakt_serializd_sync.exceptions import (
    SerializdAuthError,
    SerializdEmptySeasonError,
    SerializdError,
)
from trakt_serializd_sync.models import (
    SerializdDiaryEntry,
    SerializdDiaryEntryRequest,
    SerializdLogEpisodesRequest,
    SerializdSeasonInfo,
    WatchActivity,
)


class SerializdClient:
    """Client for Serializd API (unofficial/reverse-engineered)."""

    def __init__(self, data_dir: Path | None = None, delay_ms: int = DEFAULT_SERIALIZD_DELAY_MS):
        self.logger = logging.getLogger(__name__)
        self.data_dir = data_dir or Path(user_data_dir("trakt-serializd-sync"))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.token_file = self.data_dir / "serializd_token.json"
        self.delay_ms = delay_ms
        
        self.session = httpx.Client(base_url=SERIALIZD_BASE_URL, timeout=30.0)
        self.session.headers.update({
            'Origin': SERIALIZD_FRONT_URL,
            'Referer': SERIALIZD_FRONT_URL,
            'X-Requested-With': SERIALIZD_APP_ID,
        })
        
        self._username: str | None = None
        self._last_request_time: float = 0
        
        # Cache for season IDs (TMDB show_id -> season_number -> Serializd season_id)
        self._season_cache: dict[int, dict[int, int]] = {}

    def _rate_limit_delay(self) -> None:
        """Apply rate limiting delay between requests."""
        if self.delay_ms > 0:
            elapsed = (time.time() - self._last_request_time) * 1000
            if elapsed < self.delay_ms:
                time.sleep((self.delay_ms - elapsed) / 1000)
        self._last_request_time = time.time()

    def load_saved_token(self) -> bool:
        """Load saved token from disk. Returns True if successful."""
        if not self.token_file.exists():
            return False
        
        try:
            token_data = json.loads(self.token_file.read_text())
            access_token = token_data.get("token")
            username = token_data.get("username")
            
            if not access_token:
                return False
            
            # Validate token and get username
            is_valid, validated_username = self.validate_token(access_token)
            if not is_valid:
                self.logger.warning("Serializd token is invalid or expired")
                return False
            
            self._load_token(access_token)
            # Prefer username from validation, fall back to saved one
            self._username = validated_username or username
            return True
        except (json.JSONDecodeError, KeyError) as e:
            self.logger.warning(f"Failed to load Serializd token: {e}")
            return False

    def _load_token(self, access_token: str) -> None:
        """Set the access token cookie."""
        self._access_token = access_token
        self.session.headers.update({
            "Authorization": f"Bearer {access_token}",
        })

    def save_token(self, access_token: str, username: str) -> None:
        """Save token data to disk."""
        token_data = {
            "token": access_token,
            "username": username,
            "saved_at": datetime.now().isoformat(),
        }
        self.token_file.write_text(json.dumps(token_data, indent=2))
        self.logger.info("Serializd token saved")

    def validate_token(self, access_token: str) -> tuple[bool, str | None]:
        """Check if a token is still valid. Returns (is_valid, username)."""
        self._rate_limit_delay()
        
        resp = self.session.post(
            '/validateauthtoken',
            content=json.dumps({"token": access_token}),
            headers={"Content-Type": "application/json"},
        )
        
        if not resp.is_success:
            return False, None
        
        try:
            data = resp.json()
            is_valid = data.get("isValid", False)
            username = data.get("username")
            return is_valid, username
        except Exception:
            return False, None

    def login(self, email: str | None = None, password: str | None = None) -> str:
        """
        Log in to Serializd with email/password.
        
        Args:
            email: Account email (or from ~/keys/trakt-serializd-sync.env).
            password: Account password (or from ~/keys/trakt-serializd-sync.env).
        
        Returns:
            Access token.
        
        Raises:
            SerializdAuthError: If login fails.
        """
        # Import here to avoid circular imports
        from trakt_serializd_sync.config import get_serializd_credentials
        
        if not email or not password:
            env_email, env_password = get_serializd_credentials()
            email = email or env_email
            password = password or env_password
        
        if not email or not password:
            raise SerializdAuthError(
                "Email and password required. "
                "Set SERIALIZD_EMAIL and SERIALIZD_PASSWORD in ~/keys/trakt-serializd-sync.env"
            )
        
        self._rate_limit_delay()
        
        resp = self.session.post(
            '/login',
            data=json.dumps({"email": email, "password": password}),
        )
        if not resp.is_success:
            raise SerializdAuthError(f"Login failed: {resp.status_code}")
        
        try:
            data = resp.json()
            token = data.get("token")
            username = data.get("user", {}).get("username", "")
            
            if not token:
                raise SerializdAuthError("No token in response")
            
            self._load_token(token)
            self._username = username
            self.save_token(token, username)
            return token
        except json.JSONDecodeError as e:
            raise SerializdAuthError(f"Invalid response: {e}") from e

    @property
    def username(self) -> str:
        """Get the authenticated user's username."""
        if self._username is None:
            raise SerializdAuthError("Not authenticated")
        return self._username

    def check_season_availability(
        self,
        show_id: int,
        season_number: int,
    ) -> tuple[bool, str | None, int | None]:
        """
        Check if a season is available on Serializd for logging.
        
        Args:
            show_id: TMDB show ID.
            season_number: Season number.
        
        Returns:
            Tuple of (is_available, exclusion_reason, season_id).
            If unavailable, exclusion_reason explains why.
        """
        # Year-based seasons (2000+) likely have no TMDB equivalent
        if season_number >= 2000:
            return (
                False,
                f"year_based_season:S{season_number}",
                None,
            )
        
        # Check cache first
        if show_id in self._season_cache:
            if season_number in self._season_cache[show_id]:
                return True, None, self._season_cache[show_id][season_number]
        
        self._rate_limit_delay()
        
        resp = self.session.get(f'/show/{show_id}/season/{season_number}')
        
        if resp.status_code == 404:
            return False, "season_not_found", None
        
        if not resp.is_success:
            # Transient error - don't exclude
            return False, None, None
        
        try:
            data = resp.json()
            season_id = data.get("seasonId")
            episodes = data.get("episodes", [])
            
            if season_id is None:
                return False, "season_not_on_serializd", None
            
            if not episodes:
                return False, "serializd_no_episode_data", None
            
            # Cache the result
            if show_id not in self._season_cache:
                self._season_cache[show_id] = {}
            self._season_cache[show_id][season_number] = season_id
            
            return True, None, season_id
        except json.JSONDecodeError:
            return False, None, None

    def get_season_id(self, show_id: int, season_number: int) -> int:
        """
        Get the Serializd internal season ID for a TMDB show and season.
        
        Args:
            show_id: TMDB show ID.
            season_number: Season number.
        
        Returns:
            Serializd internal season ID.
        
        Raises:
            SerializdEmptySeasonError: If season doesn't exist or is empty.
        """
        # Check cache first
        if show_id in self._season_cache:
            if season_number in self._season_cache[show_id]:
                return self._season_cache[show_id][season_number]
        
        self._rate_limit_delay()
        
        resp = self.session.get(f'/show/{show_id}/season/{season_number}')
        
        if not resp.is_success:
            raise SerializdError(f"Failed to get season info: {resp.status_code}")
        
        try:
            data = resp.json()
            season_id = data.get("seasonId")
            
            if season_id is None:
                raise SerializdEmptySeasonError(
                    f"Season {season_number} of show {show_id} not found or empty"
                )
            
            # Cache the result
            if show_id not in self._season_cache:
                self._season_cache[show_id] = {}
            self._season_cache[show_id][season_number] = season_id
            
            return season_id
        except json.JSONDecodeError as e:
            raise SerializdError(f"Invalid response: {e}") from e

    def get_diary_entries(
        self,
        since: datetime | None = None,
    ) -> list[SerializdDiaryEntry]:
        """
        Fetch all diary entries for the authenticated user.
        
        Args:
            since: Only return entries added after this timestamp.
        
        Returns:
            List of diary entries.
        """
        all_entries: list[SerializdDiaryEntry] = []
        page = 1
        found_old_entry = False
        consecutive_old_pages = 0
        
        while True:
            self._rate_limit_delay()
            
            resp = self.session.get(
                f'/user/{self.username}/diary',
                params={'page': page},
            )
            
            if not resp.is_success:
                raise SerializdError(f"Failed to get diary: {resp.status_code}")
            
            try:
                data = resp.json()
                reviews = data.get("reviews", [])
                
                if not reviews:
                    break
                
                page_has_new = False
                for entry_data in reviews:
                    try:
                        entry = SerializdDiaryEntry(**entry_data)
                        
                        # Filter by timestamp if specified
                        # NOTE: Serializd API doesn't guarantee sorted order,
                        # so we can't stop early on first old entry
                        if since and entry.date_added <= since:
                            found_old_entry = True
                            continue
                        
                        page_has_new = True
                        all_entries.append(entry)
                    except Exception as e:
                        self.logger.warning(f"Failed to parse diary entry: {e}")
                        continue
                
                # Optimization: if we've found old entries and this page has no new ones,
                # we can likely stop (but check a couple more pages to be safe)
                if since and found_old_entry and not page_has_new:
                    consecutive_old_pages += 1
                    if consecutive_old_pages >= 2:
                        break
                else:
                    consecutive_old_pages = 0
                
                total_pages = data.get("totalPages", 1)
                if page >= total_pages:
                    break
                
                page += 1
                self.logger.debug(f"Fetched {len(all_entries)} diary entries...")
                
            except json.JSONDecodeError as e:
                raise SerializdError(f"Invalid response: {e}") from e
        
        return all_entries

    def log_episode(
        self,
        show_id: int,
        season_id: int,
        episode_number: int,
    ) -> bool:
        """
        Mark an episode as watched (without diary entry).
        
        Args:
            show_id: TMDB show ID.
            season_id: Serializd internal season ID.
            episode_number: Episode number.
        
        Returns:
            True if successful.
        """
        self._rate_limit_delay()
        
        request = SerializdLogEpisodesRequest(
            episode_numbers=[episode_number],
            season_id=season_id,
            show_id=show_id,
        )
        
        resp = self.session.post(
            '/episode_log/add',
            data=request.model_dump_json(),
        )
        
        if not resp.is_success:
            self.logger.error(f"Failed to log episode: {resp.status_code}")
            return False
        
        return True

    def add_diary_entry(self, activity: WatchActivity, mark_watched: bool = True) -> bool:
        """
        Add a watch activity to the diary with a specific date.
        
        Args:
            activity: The watch activity to add.
            mark_watched: Also mark the episode as watched.
        
        Returns:
            True if successful.
        """
        try:
            season_id = self.get_season_id(activity.tmdb_show_id, activity.season_number)
        except SerializdEmptySeasonError as e:
            self.logger.warning(f"Skipping: {e}")
            return False
        
        # First mark as watched if requested
        if mark_watched:
            try:
                self.log_episode(activity.tmdb_show_id, season_id, activity.episode_number)
            except Exception as e:
                self.logger.warning(f"Failed to mark episode watched: {e}")
        
        # Then add diary entry
        self._rate_limit_delay()
        
        request = SerializdDiaryEntryRequest.from_activity(activity, season_id)
        
        resp = self.session.post(
            '/show/reviews/add',
            data=request.model_dump_json(),
        )
        
        if not resp.is_success:
            self.logger.error(
                f"Failed to add diary entry for {activity.tmdb_show_id} "
                f"S{activity.season_number:02d}E{activity.episode_number:02d}: "
                f"{resp.status_code}"
            )
            return False
        
        return True

    def get_user_progress(self, show_id: int) -> dict[str, Any]:
        """
        Get the user's watch progress for a show.
        
        Args:
            show_id: TMDB show ID.
        
        Returns:
            Progress data with watched episodes per season.
        """
        self._rate_limit_delay()
        
        resp = self.session.get(f'/user/{self.username}/show/{show_id}/progress')
        
        if not resp.is_success:
            return {}
        
        try:
            return resp.json()
        except Exception:
            return {}

    def is_episode_watched(
        self,
        show_id: int,
        season_number: int,
        episode_number: int,
    ) -> bool:
        """Check if an episode is marked as watched."""
        progress = self.get_user_progress(show_id)
        
        if not progress:
            return False
        
        watched_seasons = progress.get("watchedSeasons", [])
        for season in watched_seasons:
            if season.get("seasonNumber") == season_number:
                watched_episodes = season.get("watchedEpisodes", [])
                return episode_number in watched_episodes
        
        return False
