"""Error reports for the private beta: the shape of an error, never its text.

An error message can quote a file name, a folder, a project or a provider's output, so it stays on the Mac. What a
report carries is the error's class name (``UnboundLocalError``, ``Rejected``), where in Dervo's own backend modules it
was raised (a file name without its folder, the function and the line, from the innermost frame in this folder), the
host operation or renderer screen that was running, and the provider for a coding task. Every value is checked against
a fixed shape here, and again by the server (supabase/migrations/202609190004_dervo_error_reports.sql). Frames outside
Dervo's own files, such as the standard library or a provider tool, are never named.

Reports go with usage sharing: the same switch, the same random install id, the same queue and the same sends
(usage_events.UsageEvents.record_error). The same error seen again before a send adds to its count, so a crash loop is
one row with a number.

Where they come from: the host's request loop (every exception a request raises), the renderer's root error boundary
(error_event, its type and screen only), and coding tasks that fail inside Dervo rather than with a provider's error.
"""
from pathlib import Path
import re

BACKEND = Path(__file__).resolve().parent
SOURCES = ('host', 'renderer', 'task')
PROVIDERS = ('claude', 'codex')
TYPE = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,59}')
FILE = re.compile(r'[a-z_][a-z0-9_]{0,59}\.py')
FUNCTION = re.compile(r'[A-Za-z_<][A-Za-z0-9_.<>]{0,79}')
OPERATION = re.compile(r'[a-z_][a-z0-9_.]{0,39}')
# The renderer's error names: the built-in JavaScript and DOM ones. Anything else, including a class a library defines,
# is sent as Error.
RENDERER_TYPES = ('Error', 'TypeError', 'RangeError', 'ReferenceError', 'SyntaxError', 'URIError', 'EvalError', 'AggregateError',
                  'SecurityError', 'NotFoundError', 'NotAllowedError', 'InvalidStateError', 'AbortError', 'DataCloneError',
                  'QuotaExceededError', 'NetworkError', 'ChunkLoadError')
FIELDS = ('source', 'error_type', 'file', 'function', 'line', 'operation', 'provider')

_sink = None


def install(sink):
    """The host's UsageEvents, which holds reports until a send. None stops capturing."""
    global _sink
    _sink = sink


def location(error):
    """The innermost frame in one of Dervo's own backend files, as (file name, function, line), or None."""
    found = None
    trace = getattr(error, '__traceback__', None)
    while trace is not None:
        code = trace.tb_frame.f_code
        try:
            path = Path(code.co_filename).resolve()
        except (OSError, ValueError, RuntimeError):
            path = None
        if path is not None and path.parent == BACKEND and FILE.fullmatch(path.name):
            name = getattr(code, 'co_qualname', code.co_name)
            if not FUNCTION.fullmatch(name):
                name = code.co_name
            if FUNCTION.fullmatch(name) and 1 <= trace.tb_lineno <= 100000:
                found = (path.name, name, trace.tb_lineno)
        trace = trace.tb_next
    return found


def row(source, error_type, where=None, operation=None, provider=None):
    """A report with every field checked, or None when the error itself can't be described safely."""
    if source not in SOURCES or type(error_type) is not str or not TYPE.fullmatch(error_type):
        return None
    file = function = line = None
    if where is not None:
        file, function, line = where
        if (type(file) is not str or not FILE.fullmatch(file) or type(function) is not str or not FUNCTION.fullmatch(function)
                or type(line) is not int or isinstance(line, bool) or not 1 <= line <= 100000):
            file = function = line = None
    if type(operation) is not str or not OPERATION.fullmatch(operation):
        operation = None
    if provider not in PROVIDERS:
        provider = None
    return {'source': source, 'error_type': error_type, 'file': file, 'function': function, 'line': line,
            'operation': operation, 'provider': provider}


def from_exception(error, source='host', operation=None, provider=None):
    return row(source, type(error).__name__, location(error), operation, provider)


def from_renderer(props):
    """The crash screen's report: a built-in error name and one of the usage screens. Nothing else is read."""
    if not isinstance(props, dict):
        return None
    from usage_events import SCREENS
    error_type = props.get('errorType')
    screen = props.get('screen')
    return row('renderer', error_type if error_type in RENDERER_TYPES else 'Error', None,
               screen if screen in SCREENS else None)


def capture(error, *, source='host', operation=None, provider=None):
    """Note an error from the host's own code. Never raises: a report is never worth a second failure."""
    try:
        sink = _sink
        if sink is None:
            return False
        report = from_exception(error, source, operation, provider)
        return report is not None and sink.record_error(report)
    except Exception:
        return False
