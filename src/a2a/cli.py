import argparse
import logging
import os

from importlib.resources import files


try:
    from alembic import command
    from alembic.config import Config

except ImportError as e:
    raise ImportError(
        "CLI requires Alembic. Install with: 'pip install a2a-sdk[cli]'."
    ) from e


def _add_shared_args(
    parser: argparse.ArgumentParser, is_sub: bool = False
) -> None:
    """Add common arguments to the given parser."""
    prefix = 'sub_' if is_sub else ''
    parser.add_argument(
        '-u',
        '--database-url',
        dest=f'{prefix}database_url',
        help='Database URL to use for the migrations. If not set, the DATABASE_URL environment variable will be used.',
    )
    parser.add_argument(
        '-t',
        '--table',
        dest=f'{prefix}table',
        help="Specific table to update. If not set, both 'tasks' and 'push_notification_configs' are updated.",
        action='append',
    )
    parser.add_argument(
        '-v',
        '--verbose',
        dest=f'{prefix}verbose',
        help='Enable verbose output (sets sqlalchemy.engine logging to INFO)',
        action='store_true',
    )
    parser.add_argument(
        '--sql',
        dest=f'{prefix}sql',
        help='Run migrations in sql mode (generate SQL instead of executing)',
        action='store_true',
    )


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser for the migration tool."""
    parser = argparse.ArgumentParser(description='A2A Database Migration Tool')

    # Global options
    parser.add_argument(
        '-o',
        '--owner',
        help="Value for the 'owner' column (used in specific migrations). If not set defaults to 'unknown'",
    )
    _add_shared_args(parser)

    subparsers = parser.add_subparsers(dest='cmd', help='Migration command')

    # Upgrade command
    up_parser = subparsers.add_parser(
        'upgrade', help='Upgrade to a later version'
    )
    up_parser.add_argument(
        'revision',
        nargs='?',
        default='head',
        help='Revision target (default: head)',
    )
    up_parser.add_argument(
        '-o', '--owner', dest='sub_owner', help='Alias for top-level --owner'
    )
    _add_shared_args(up_parser, is_sub=True)

    # Downgrade command
    down_parser = subparsers.add_parser(
        'downgrade', help='Revert to a previous version'
    )
    down_parser.add_argument(
        'revision',
        nargs='?',
        default='base',
        help='Revision target (e.g., -1, base or a specific ID)',
    )
    _add_shared_args(down_parser, is_sub=True)

    return parser


def run_migrations() -> None:
    """CLI tool to manage database migrations."""
    # Configure logging to show INFO messages
    logging.basicConfig(level=logging.INFO, format='%(levelname)s  %(message)s')

    parser = create_parser()
    args = parser.parse_args()

    # Default to upgrade head if no command is provided
    if not args.cmd:
        args.cmd = 'upgrade'
        args.revision = 'head'

    # Locate the bundled alembic.ini
    ini_path = files('a2a').joinpath('alembic.ini')
    cfg = Config(str(ini_path))

    # Dynamically set the script location
    migrations_path = files('a2a').joinpath('migrations')
    cfg.set_main_option('script_location', str(migrations_path))

    # Consolidate owner, db_url, tables, verbose and sql values
    owner = args.owner or getattr(args, 'sub_owner', None)
    db_url = args.database_url or getattr(args, 'sub_database_url', None)
    tables = args.table or getattr(args, 'sub_table', None)
    verbose = args.verbose or getattr(args, 'sub_verbose', False)
    sql = args.sql or getattr(args, 'sub_sql', False)

    # Pass custom arguments to the migration context
    if owner:
        if args.cmd == 'downgrade':
            parser.error(
                "The --owner option is not supported for the 'downgrade' command."
            )
        cfg.set_main_option('owner', owner)
    if db_url:
        os.environ['DATABASE_URL'] = db_url
    if tables:
        cfg.set_main_option('tables', ','.join(tables))
    if verbose:
        cfg.set_main_option('verbose', 'true')

    # Execute the requested command
    if args.cmd == 'upgrade':
        logging.info('Upgrading database to %s', args.revision)
        command.upgrade(cfg, args.revision, sql=sql)
    elif args.cmd == 'downgrade':
        logging.info('Downgrading database to %s', args.revision)
        command.downgrade(cfg, args.revision, sql=sql)

    logging.info('Done.')
