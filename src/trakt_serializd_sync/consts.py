# AI-generated: Constants and configuration
"""Constants for Trakt and Serializd API access."""

# Trakt API configuration
# Using the existing trakt-to-serializd OAuth app credentials
TRAKT_CLIENT_ID = 'b2a29fa870cadf9ba61dc8bddde9722875bd2390293ad36c3fedc8a6ddd7a8e7'
TRAKT_CLIENT_SECRET = '17c7a5441b15376c089069209a32764c02ea600a12d1c3029c50ac62e419427b'
TRAKT_REDIRECT_URI = 'urn:ietf:wg:oauth:2.0:oob'
TRAKT_BASE_URL = 'https://api.trakt.tv'

# Serializd API configuration (unofficial/reverse-engineered)
SERIALIZD_BASE_URL = 'https://serializd.onrender.com/api/'
SERIALIZD_FRONT_URL = 'https://www.serializd.com'
SERIALIZD_APP_ID = 'serializd_vercel'

# Sync configuration defaults
DEFAULT_SYNC_INTERVAL_MINUTES = 15
DEFAULT_SERIALIZD_DELAY_MS = 200  # Delay between Serializd API calls
TRAKT_RATE_LIMIT_CALLS = 1000  # Max calls per 5 minutes
TRAKT_RATE_LIMIT_PERIOD = 300  # 5 minutes in seconds
