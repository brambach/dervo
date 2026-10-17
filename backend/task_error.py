"""Provider errors as text for the interface. No imports, so any reader of saved records can use it."""


def error_text(error):
    """Provider errors are shown to the person, so they cross this boundary as text.

    A structured provider error carries its own extras beside the message, such as
    Codex's additionalDetails, codexErrorInfo and misalignment. Those can quote
    commands or arguments, so only the message is kept and the rest is dropped
    rather than serialised into the interface.
    """
    if isinstance(error, dict):
        message = error.get('message')
        if isinstance(message, str) and message.strip():
            return message[:400]
        return 'The provider reported an error without a message.'
    return '' if error is None else str(error)[:400]


def task_error(value):
    """A task's error is shown directly in the interface, so it is text or None.

    Records saved before provider errors were coerced can hold a structured
    provider error object, which crashes the renderer. Reading repairs them.
    """
    return value if value is None or isinstance(value, str) else error_text(value)
