"""
Configuration loading and argument parsing for LinkedIn MCP Server.

Loads settings from CLI arguments and environment variables.
"""

import argparse
import logging
import os
import sys
from typing import Literal, cast

from dotenv import load_dotenv

from .schema import AppConfig, ConfigurationError

# Load .env file if present
load_dotenv()

logger = logging.getLogger(__name__)

# Boolean value mappings for environment variable parsing
TRUTHY_VALUES = ("1", "true", "yes", "on")
FALSY_VALUES = ("0", "false", "no", "off")


def _normalize_env(value: str) -> str:
    """Normalize environment variable values for tolerant parsing."""
    return value.strip().lower()


def positive_int(value: str) -> int:
    """Argparse type for positive integers."""
    ivalue = int(value)
    if ivalue <= 0:
        raise argparse.ArgumentTypeError(f"must be positive, got {value}")
    return ivalue


class EnvironmentKeys:
    """Environment variable names used by the application."""

    HEADLESS = "HEADLESS"
    LOG_LEVEL = "LOG_LEVEL"
    TRANSPORT = "TRANSPORT"
    TIMEOUT = "TIMEOUT"
    USER_AGENT = "USER_AGENT"
    HOST = "HOST"
    PORT = "PORT"
    HTTP_PATH = "HTTP_PATH"
    SLOW_MO = "SLOW_MO"
    VIEWPORT = "VIEWPORT"
    CHROME_PATH = "CHROME_PATH"
    USER_DATA_DIR = "USER_DATA_DIR"
    BROWSER_CDP_ENDPOINT = "BROWSER_CDP_ENDPOINT"


def is_interactive_environment() -> bool:
    """
    Detect if running in an interactive environment (TTY).

    Returns:
        True if both stdin and stdout are TTY devices
    """
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, OSError):
        return False


def load_from_env(config: AppConfig) -> AppConfig:
    """Load configuration from environment variables."""

    # Log level
    if log_level_env := os.environ.get(EnvironmentKeys.LOG_LEVEL):
        log_level_upper = log_level_env.strip().upper()
        if log_level_upper in ("DEBUG", "INFO", "WARNING", "ERROR"):
            config.server.log_level = cast(
                Literal["DEBUG", "INFO", "WARNING", "ERROR"], log_level_upper
            )

    # Headless mode
    if headless_env := os.environ.get(EnvironmentKeys.HEADLESS):
        headless_value = _normalize_env(headless_env)
        if headless_value in FALSY_VALUES:
            config.browser.headless = False
        elif headless_value in TRUTHY_VALUES:
            config.browser.headless = True

    # Transport mode
    if transport_env := os.environ.get(EnvironmentKeys.TRANSPORT):
        config.server.transport_explicitly_set = True
        transport_value = _normalize_env(transport_env)
        if transport_value == "stdio":
            config.server.transport = "stdio"
        elif transport_value == "streamable-http":
            config.server.transport = "streamable-http"
        else:
            raise ConfigurationError(
                f"Invalid TRANSPORT: '{transport_env}'. Must be 'stdio' or 'streamable-http'."
            )

    # Persistent browser profile directory
    if user_data_dir := os.environ.get(EnvironmentKeys.USER_DATA_DIR):
        config.browser.user_data_dir = user_data_dir

    # Timeout for page operations (validated in BrowserConfig.validate())
    if timeout_env := os.environ.get(EnvironmentKeys.TIMEOUT):
        try:
            config.browser.default_timeout = int(timeout_env)
        except ValueError:
            raise ConfigurationError(
                f"Invalid TIMEOUT: '{timeout_env}'. Must be an integer."
            )

    # Custom user agent
    if user_agent_env := os.environ.get(EnvironmentKeys.USER_AGENT):
        config.browser.user_agent = user_agent_env

    # HTTP server host
    if host_env := os.environ.get(EnvironmentKeys.HOST):
        config.server.host = host_env

    # HTTP server port (validated in AppConfig.validate())
    if port_env := os.environ.get(EnvironmentKeys.PORT):
        try:
            config.server.port = int(port_env)
        except ValueError:
            raise ConfigurationError(f"Invalid PORT: '{port_env}'. Must be an integer.")

    # HTTP server path
    if path_env := os.environ.get(EnvironmentKeys.HTTP_PATH):
        config.server.path = path_env

    # Slow motion delay for debugging (validated in BrowserConfig.validate())
    if slow_mo_env := os.environ.get(EnvironmentKeys.SLOW_MO):
        try:
            config.browser.slow_mo = int(slow_mo_env)
        except ValueError:
            raise ConfigurationError(
                f"Invalid SLOW_MO: '{slow_mo_env}'. Must be an integer."
            )

    # Browser viewport (validated in BrowserConfig.validate())
    if viewport_env := os.environ.get(EnvironmentKeys.VIEWPORT):
        try:
            width, height = viewport_env.lower().split("x")
            config.browser.viewport_width = int(width)
            config.browser.viewport_height = int(height)
        except ValueError:
            raise ConfigurationError(
                f"Invalid VIEWPORT: '{viewport_env}'. Must be in format WxH (e.g., 1280x720)."
            )

    # Custom Chrome/Chromium executable path
    if chrome_path_env := os.environ.get(EnvironmentKeys.CHROME_PATH):
        config.browser.chrome_path = chrome_path_env

    # Attach to an already-running Chrome/Chromium with remote debugging enabled.
    if cdp_endpoint_env := os.environ.get(EnvironmentKeys.BROWSER_CDP_ENDPOINT):
        config.browser.cdp_endpoint = cdp_endpoint_env

    return config


def load_from_args(config: AppConfig) -> AppConfig:
    """Load configuration from command line arguments."""
    parser = argparse.ArgumentParser(
        description="LinkedIn MCP Server - A Model Context Protocol server for LinkedIn integration"
    )

    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run browser with a visible window (useful for login and debugging)",
    )

    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging level (default: WARNING)",
    )

    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=None,
        help="Specify the transport mode (stdio or streamable-http)",
    )

    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="HTTP server host (default: 127.0.0.1)",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="HTTP server port (default: 8000)",
    )

    parser.add_argument(
        "--path",
        type=str,
        default=None,
        help="HTTP server path (default: /mcp)",
    )

    # Browser configuration
    parser.add_argument(
        "--slow-mo",
        type=int,
        default=0,
        metavar="MS",
        help="Slow down browser actions by N milliseconds (debugging)",
    )

    parser.add_argument(
        "--user-agent",
        type=str,
        default=None,
        help="Custom browser user agent",
    )

    parser.add_argument(
        "--viewport",
        type=str,
        default=None,
        metavar="WxH",
        help="Browser viewport size (default: 1280x720)",
    )

    parser.add_argument(
        "--timeout",
        type=positive_int,
        default=None,
        metavar="MS",
        help="Browser timeout for page operations in milliseconds (default: 5000)",
    )

    parser.add_argument(
        "--chrome-path",
        type=str,
        default=None,
        metavar="PATH",
        help="Path to Chrome/Chromium executable (for custom browser installations)",
    )

    parser.add_argument(
        "--browser-cdp-endpoint",
        type=str,
        default=None,
        metavar="URL",
        help=(
            "Attach to an already-running Chrome/Chromium via CDP "
            "(for example http://127.0.0.1:9222)"
        ),
    )

    # Session management
    parser.add_argument(
        "--login",
        action="store_true",
        help="Login interactively via browser and save persistent profile",
    )

    parser.add_argument(
        "--status",
        action="store_true",
        help="Check if current session is valid and exit",
    )

    parser.add_argument(
        "--logout",
        action="store_true",
        help="Clear stored LinkedIn browser profile",
    )

    parser.add_argument(
        "--user-data-dir",
        type=str,
        default=None,
        metavar="PATH",
        help="Path to persistent browser profile directory (default: ~/.linkedin-mcp/profile)",
    )

    subparsers = parser.add_subparsers(dest="cli_command")

    post_details_parser = subparsers.add_parser(
        "post-details",
        help="Scrape details for one LinkedIn feed post URL",
    )
    post_details_parser.add_argument("post_url")
    post_details_parser.add_argument("--output", type=str, default=None)

    post_comments_parser = subparsers.add_parser(
        "post-comments",
        help="Scrape comments for one LinkedIn feed post URL",
    )
    post_comments_parser.add_argument("post_url")
    post_comments_parser.add_argument("--limit", type=int, default=20)
    post_comments_parser.add_argument("--output", type=str, default=None)

    post_reactors_parser = subparsers.add_parser(
        "post-reactors",
        help="Scrape reactors for one LinkedIn feed post URL",
    )
    post_reactors_parser.add_argument("post_url")
    post_reactors_parser.add_argument("--limit", type=int, default=50)
    post_reactors_parser.add_argument("--reaction-type", type=str, default=None)
    post_reactors_parser.add_argument("--output", type=str, default=None)

    company_engagement_parser = subparsers.add_parser(
        "company-engagement",
        help="Scrape bounded recent post engagement for a LinkedIn company",
    )
    company_engagement_parser.add_argument("company_name")
    company_engagement_parser.add_argument("--limit", type=int, default=3)
    company_engagement_parser.add_argument(
        "--comments",
        dest="include_comments",
        action="store_true",
        default=True,
        help="Include comments (default)",
    )
    company_engagement_parser.add_argument(
        "--no-comments",
        dest="include_comments",
        action="store_false",
        help="Skip comments",
    )
    company_engagement_parser.add_argument(
        "--reactors",
        dest="include_reactors",
        action="store_true",
        default=False,
        help="Include reactors/likers",
    )
    company_engagement_parser.add_argument("--comment-limit", type=int, default=20)
    company_engagement_parser.add_argument("--reactor-limit", type=int, default=0)
    company_engagement_parser.add_argument("--reaction-type", type=str, default=None)
    company_engagement_parser.add_argument("--output", type=str, default=None)

    search_feed_parser = subparsers.add_parser(
        "search-feed-posts",
        help="Search the authenticated LinkedIn home feed for matching posts",
    )
    search_feed_parser.add_argument("--keyword", dest="keywords", action="append")
    search_feed_parser.add_argument("--max-posts", type=int, default=10)
    search_feed_parser.add_argument("--scrolls", type=int, default=10)
    search_feed_parser.add_argument("--min-reactions", type=int, default=0)
    search_feed_parser.add_argument("--min-comments", type=int, default=0)
    search_feed_parser.add_argument(
        "--include-promoted",
        action="store_true",
        default=False,
        help="Include promoted feed posts",
    )
    search_feed_parser.add_argument("--output", type=str, default=None)

    feed_engagement_parser = subparsers.add_parser(
        "feed-engagement",
        help="Search the home feed and enrich matching posts with engagement",
    )
    feed_engagement_parser.add_argument("--keyword", dest="keywords", action="append")
    feed_engagement_parser.add_argument("--max-posts", type=int, default=5)
    feed_engagement_parser.add_argument("--scrolls", type=int, default=10)
    feed_engagement_parser.add_argument("--min-reactions", type=int, default=0)
    feed_engagement_parser.add_argument("--min-comments", type=int, default=0)
    feed_engagement_parser.add_argument(
        "--include-promoted",
        action="store_true",
        default=False,
        help="Include promoted feed posts",
    )
    feed_engagement_parser.add_argument(
        "--comments",
        dest="include_comments",
        action="store_true",
        default=True,
        help="Include comments (default)",
    )
    feed_engagement_parser.add_argument(
        "--no-comments",
        dest="include_comments",
        action="store_false",
        help="Skip comments",
    )
    feed_engagement_parser.add_argument(
        "--reactors",
        dest="include_reactors",
        action="store_true",
        default=False,
        help="Include reactors/likers",
    )
    feed_engagement_parser.add_argument("--comment-limit", type=int, default=20)
    feed_engagement_parser.add_argument("--reactor-limit", type=int, default=0)
    feed_engagement_parser.add_argument("--reaction-type", type=str, default=None)
    feed_engagement_parser.add_argument("--output", type=str, default=None)

    args = parser.parse_args()

    # Update configuration with parsed arguments
    if args.no_headless:
        config.browser.headless = False

    if args.log_level:
        config.server.log_level = args.log_level

    if args.transport:
        config.server.transport = args.transport
        config.server.transport_explicitly_set = True

    if args.host:
        config.server.host = args.host

    if args.port:
        config.server.port = args.port

    if args.path:
        config.server.path = args.path

    # Browser configuration
    if args.slow_mo:
        config.browser.slow_mo = args.slow_mo

    if args.user_agent:
        config.browser.user_agent = args.user_agent

    # Viewport (validated in BrowserConfig.validate())
    if args.viewport:
        try:
            width, height = args.viewport.lower().split("x")
            config.browser.viewport_width = int(width)
            config.browser.viewport_height = int(height)
        except ValueError:
            raise ConfigurationError(
                f"Invalid --viewport: '{args.viewport}'. Must be in format WxH (e.g., 1280x720)."
            )

    if args.timeout is not None:
        config.browser.default_timeout = args.timeout

    if args.chrome_path:
        config.browser.chrome_path = args.chrome_path

    if args.browser_cdp_endpoint:
        config.browser.cdp_endpoint = args.browser_cdp_endpoint

    # Session management
    if args.login:
        config.server.login = True

    if args.status:
        config.server.status = True

    if args.logout:
        config.server.logout = True

    if args.user_data_dir:
        config.browser.user_data_dir = args.user_data_dir

    if args.cli_command:
        config.server.cli_command = args.cli_command
        command_args = vars(args).copy()
        for key in (
            "no_headless",
            "log_level",
            "transport",
            "host",
            "port",
            "path",
            "slow_mo",
            "user_agent",
            "viewport",
            "timeout",
            "chrome_path",
            "browser_cdp_endpoint",
            "login",
            "status",
            "logout",
            "user_data_dir",
            "cli_command",
        ):
            command_args.pop(key, None)
        config.server.cli_args = command_args

    return config


def load_config() -> AppConfig:
    """
    Load configuration with clear precedence order.

    Configuration is loaded in the following priority order:
    1. Command line arguments (highest priority)
    2. Environment variables
    3. Defaults (lowest priority)

    Returns:
        Fully configured application settings
    """
    # Start with default configuration
    config = AppConfig()

    # Set interactive mode
    config.is_interactive = is_interactive_environment()
    logger.debug(f"Interactive mode: {config.is_interactive}")

    # Override with environment variables
    config = load_from_env(config)

    # Override with command line arguments (highest priority)
    config = load_from_args(config)

    # Validate final configuration
    config.validate()

    return config
