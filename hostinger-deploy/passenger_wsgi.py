"""Entry point Hostinger's Python App feature (Phusion Passenger) looks
for by this exact filename. Must expose a WSGI callable named
`application` at module scope -- that's wsgi_app.py's Flask app."""

from wsgi_app import application  # noqa: F401
