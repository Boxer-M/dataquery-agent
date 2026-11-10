"""Versioned SQL metric assets; independent of SQL Guard and Gemini."""
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read_queries(path: Path) -> dict[str, str]:
    """Read one named statement per `-- metric:` or `-- check:` section."""
    parts = re.split(r'^-- (?:metric|check): ([a-z_]+)\s*$', path.read_text(), flags=re.M)
    result = {}
    for name, statement in zip(parts[1::2], parts[2::2]):
        if name in result:
            raise ValueError(f'Duplicate SQL identifier: {name}')
        result[name] = statement.strip()
    if not result:
        raise ValueError(f'No named queries in {path}')
    return result


def window_parameters(start: str, end: str, as_of: str) -> dict[str, datetime]:
    params = {key: datetime.fromisoformat(value) for key, value in
              [('start', start), ('end', end), ('as_of', as_of)]}
    if any(value.tzinfo is not None for value in params.values()):
        raise ValueError('Source timestamps are timezone-naive')
    if not params['start'] < params['end'] <= params['as_of']:
        raise ValueError('Require start < end <= as_of')
    return params
